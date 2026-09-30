"""调度核心：计算下一次重启时间、按提前量发送提醒、执行重启。

设计要点：

* 单独一个守护线程按 ``check_interval_seconds`` 轮询（默认 1 秒），
  每次只维护「最近的一次重启」这一个计划，避免多个计划重叠互相打架。
* 提醒的发送时间 = 真正重启时刻 - ``advance_seconds``。
  计划生成时已经过期的提醒会被标记为跳过（``skip_missed_notifications``），
  这样插件重载 / 服务器刚启动时不会被过期提醒刷屏。
* 真正的重启动作在独立的「工作线程」里执行，因为 ``server.restart()`` 是阻塞的，
  不会卡住调度循环。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from .config import Config, Notification, Schedule
from .cron import format_duration_cn
from .notify import Notifier, build_context, render

__all__ = ['Clock', 'PendingNotification', 'RestartPlan', 'RestartScheduler']


class Clock:
    """时间源；配置了时区时返回带时区的时间，否则使用进程本地时间"""

    def __init__(self, timezone_name: Optional[str] = None, logger: Optional[logging.Logger] = None):
        self._logger = logger or logging.getLogger(__name__)
        self._tz = None
        if timezone_name:
            try:
                from zoneinfo import ZoneInfo
                self._tz = ZoneInfo(timezone_name)
            except Exception as e:
                self._logger.warning(f'[scheduled_restart] 无法加载时区 {timezone_name!r}（{e}），将使用本地时区')

    @property
    def timezone(self):
        return self._tz

    def timezone_name(self) -> Optional[str]:
        return None if self._tz is None else str(self._tz)

    def now(self) -> datetime:
        if self._tz is None:
            return datetime.now()
        return datetime.now(self._tz)


@dataclass
class PendingNotification:
    """一条待发送的提醒"""

    notification: Notification
    fire_time: datetime
    index: int
    fired: bool = False
    skipped: bool = False


@dataclass
class RestartPlan:
    """最近一次待执行的重启"""

    schedule: Schedule
    restart_time: datetime
    created_at: datetime
    notifications: List[PendingNotification] = field(default_factory=list)

    def remaining_seconds(self, now: datetime) -> float:
        return max(0.0, (self.restart_time - now).total_seconds())


class RestartScheduler:
    """定时重启调度器"""

    def __init__(self, server, config: Config, *, notifier: Optional[Notifier] = None, clock: Optional[Clock] = None):
        self._server = server
        self._config = config
        self._logger: logging.Logger = getattr(server, 'logger', logging.getLogger('scheduled_restart'))
        self._notifier = notifier or Notifier(server)
        self._clock = clock or Clock(config.timezone, self._logger)

        self._lock = threading.RLock()
        self._wakeup = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._plan: Optional[RestartPlan] = None
        #: 计划名 -> 已经触发或已经被取消的重启时刻，避免同一时刻被重复执行
        self._handled: Dict[str, datetime] = {}
        self._restarting = False

        try:
            self._data_folder: Optional[str] = server.get_data_folder()
        except Exception:  # pragma: no cover - 某些环境下不可用
            self._data_folder = None

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name='ScheduledRestart', daemon=True)
            self._thread.start()
        self._logger.info(
            f'[scheduled_restart] 调度线程已启动：{len(self._config.schedules)} 个计划，'
            f'时区 {self._clock.timezone_name() or "本地时间"}'
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self._wakeup.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        with self._lock:
            self._thread = None
            self._plan = None

    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # ------------------------------------------------------------------ 主循环

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                self._logger.exception('[scheduled_restart] 调度循环出现异常')
            interval = max(0.2, float(self._config.check_interval_seconds))
            self._wakeup.wait(interval)
            self._wakeup.clear()

    def _tick(self) -> None:
        with self._lock:
            if self._stop_event.is_set():
                return
            if not self._config.enabled or self._restarting:
                self._plan = None
                return

            now = self._clock.now()
            if self._plan is None:
                self._plan = self._compute_plan(now)
                if self._plan is not None:
                    self._log_plan(self._plan)

            plan = self._plan
            if plan is None:
                return

            for item in plan.notifications:
                if item.fired or item.skipped:
                    continue
                if now >= item.fire_time:
                    item.fired = True
                    self._send(plan, item)

            if now >= plan.restart_time:
                self._plan = None
                self._handled[plan.schedule.name] = plan.restart_time
                self._start_worker(plan, manual=False, actor='')

    # ------------------------------------------------------------------ 计划

    def _compute_plan(self, now: datetime) -> Optional[RestartPlan]:
        best: Optional[RestartPlan] = None
        for schedule in self._config.schedules:
            if not schedule.enabled:
                continue
            restart_time = self._next_restart_time(schedule, now)
            if restart_time is None:
                continue
            if best is None or restart_time < best.restart_time:
                best = RestartPlan(schedule=schedule, restart_time=restart_time, created_at=now)
        if best is None:
            return None

        notifications = self._config.notifications_for(best.schedule)
        for index, notification in enumerate(notifications):
            if not notification.enabled:
                continue
            fire_time = best.restart_time - timedelta(seconds=notification.advance_seconds)
            item = PendingNotification(notification=notification, fire_time=fire_time, index=index)
            if self._config.skip_missed_notifications and fire_time < now:
                item.skipped = True
            best.notifications.append(item)
        best.notifications.sort(key=lambda item: (-item.notification.advance_seconds, item.index))
        return best

    def _next_restart_time(self, schedule: Schedule, now: datetime) -> Optional[datetime]:
        moment = schedule.cron.next_after(now)
        if moment is None:
            return None
        restart_time = moment + timedelta(seconds=schedule.restart_delay_seconds)
        if self._handled.get(schedule.name) == restart_time:
            # 这一时刻刚刚执行过（或被取消），顺延到下一次
            moment = schedule.cron.next_after(restart_time)
            if moment is None:
                return None
            restart_time = moment + timedelta(seconds=schedule.restart_delay_seconds)
        return restart_time

    def _log_plan(self, plan: RestartPlan) -> None:
        skipped = sum(1 for item in plan.notifications if item.skipped)
        suffix = f'，跳过 {skipped} 条已过期的提醒' if skipped else ''
        self._logger.info(
            f'[scheduled_restart] 下一次重启：{plan.schedule.name} '
            f'于 {plan.restart_time.strftime("%Y-%m-%d %H:%M:%S")}'
            f'（cron: {plan.schedule.cron_expression}，方式: {plan.schedule.restart_method}，'
            f'提醒 {len(plan.notifications) - skipped} 条{suffix}）'
        )

    def _send(self, plan: RestartPlan, item: PendingNotification) -> None:
        if not self._is_server_running():
            # 服务器没运行时 say / title 都会被 MCDR 丢掉（只留一条 warning），
            # 这里直接跳过，避免日志里出现「已发送」这种误导信息
            self._logger.warning(
                f'[scheduled_restart] 服务器当前未运行，跳过提醒'
                f'（计划 {plan.schedule.name}，提前 {item.notification.advance_seconds:g} 秒，'
                f'方式 {item.notification.type}）'
            )
            return
        try:
            context = build_context(
                schedule_name=plan.schedule.name,
                cron_expression=plan.schedule.cron_expression,
                restart_time=plan.restart_time,
                now=self._clock.now(),
                index=item.index + 1,
                total=len(plan.notifications),
            )
            self._notifier.send_notification(item.notification, context)
            self._logger.info(
                f'[scheduled_restart] 已发送提醒（提前 {item.notification.advance_seconds:g} 秒，'
                f'方式 {item.notification.type}，计划 {plan.schedule.name}）'
            )
        except Exception:
            self._logger.exception('[scheduled_restart] 发送提醒时出现异常')

    def _is_server_running(self) -> bool:
        """服务器是否在运行；接口异常时保守地按「运行中」处理，避免漏发提醒"""
        try:
            return bool(self._server.is_server_running())
        except Exception:
            return True

    # ------------------------------------------------------------------ 重启

    def _start_worker(self, plan: RestartPlan, *, manual: bool, actor: str) -> bool:
        with self._lock:
            if self._restarting:
                self._logger.warning('[scheduled_restart] 已有重启流程正在执行，跳过本次触发')
                return False
            self._restarting = True
        worker = threading.Thread(
            target=self._restart_worker,
            args=(plan, manual, actor),
            name='ScheduledRestart-Worker',
            daemon=True,
        )
        worker.start()
        return True

    def _restart_worker(self, plan: RestartPlan, manual: bool, actor: str) -> None:
        try:
            self._execute_restart(plan.schedule, manual=manual, actor=actor)
        except Exception:
            self._logger.exception('[scheduled_restart] 执行重启时出现异常')
        finally:
            with self._lock:
                self._restarting = False
                self._plan = None
            self._wakeup.set()

    def _execute_restart(self, schedule: Schedule, *, manual: bool, actor: str) -> bool:
        who = f'，触发者 {actor}' if manual and actor else ''
        self._logger.info(
            f'[scheduled_restart] 开始执行重启：{schedule.name}'
            f'（cron: {schedule.cron_expression}，方式: {schedule.restart_method}{who}）'
        )
        self._append_history(schedule, manual=manual, actor=actor)

        method = schedule.restart_method
        if method == 'none':
            self._logger.info('[scheduled_restart] restart_method 为 none：只发送提醒，不执行重启')
            return True

        if not self._is_server_running():
            self._logger.warning('[scheduled_restart] 服务器当前没有运行，跳过重启动作')
            return False

        if schedule.kick_players:
            message = render(schedule.kick_message, {'schedule': schedule.name}) or '服务器正在重启'
            try:
                self._server.execute(f'kick @a {message}')
            except Exception:
                self._logger.exception('[scheduled_restart] 踢出玩家失败（可能服务端版本不支持 @a 选择器）')

        ok = False
        if method == 'mcdr_restart':
            ok = bool(self._server.restart())
        elif method == 'stop':
            ok = bool(self._server.stop())
        elif method == 'stop_exit':
            ok = bool(self._server.stop_exit())
        elif method == 'custom':
            self._server.execute(schedule.custom_command)
            ok = True
            if schedule.custom_auto_start:
                self._server.wait_for_start()
                ok = bool(self._server.start())
        else:  # pragma: no cover - 配置校验已保证不会走到这里
            self._logger.error(f'[scheduled_restart] 未知的重启方式 {method!r}')

        if ok:
            self._logger.info(f'[scheduled_restart] 重启指令已下发（{schedule.name}）')
        else:
            self._logger.error(f'[scheduled_restart] 重启动作执行失败（{schedule.name}，方式 {method}）')
        return ok

    # ------------------------------------------------------------------ 历史记录

    @property
    def data_folder(self) -> Optional[str]:
        return self._data_folder

    @property
    def history_path(self) -> Optional[str]:
        if self._data_folder is None:
            return None
        return os.path.join(self._data_folder, 'history.jsonl')

    def _append_history(self, schedule: Schedule, *, manual: bool, actor: str) -> None:
        path = self.history_path
        if not self._config.log_history or path is None:
            return
        entry = {
            'time': self._clock.now().isoformat(timespec='seconds'),
            'schedule': schedule.name,
            'cron': schedule.cron_expression,
            'method': schedule.restart_method,
            'manual': manual,
            'actor': actor,
        }
        try:
            with open(path, 'a', encoding='utf8') as file_handler:
                file_handler.write(json.dumps(entry, ensure_ascii=False) + '\n')
            self._trim_history(path)
        except OSError:
            self._logger.exception('[scheduled_restart] 写入重启历史失败')

    def _trim_history(self, path: str) -> None:
        try:
            if os.path.getsize(path) < 256 * 1024:
                return
            with open(path, encoding='utf8') as file_handler:
                lines = file_handler.readlines()
            keep = lines[-max(1, int(self._config.history_size)):]
            with open(path, 'w', encoding='utf8') as file_handler:
                file_handler.writelines(keep)
        except OSError:  # pragma: no cover
            pass

    def read_history(self, count: int = 10) -> List[Dict[str, Any]]:
        path = self.history_path
        if path is None or not os.path.isfile(path):
            return []
        try:
            with open(path, encoding='utf8') as file_handler:
                lines = file_handler.readlines()
        except OSError:
            return []
        result: List[Dict[str, Any]] = []
        for line in lines[-max(1, count):]:
            line = line.strip()
            if line == '':
                continue
            try:
                result.append(json.loads(line))
            except ValueError:
                continue
        return result

    # ------------------------------------------------------------------ 对外接口

    def reload(self, config: Config, *, keep_clock: bool = False) -> None:
        """热重载配置"""
        with self._lock:
            if not keep_clock:
                self._clock = Clock(config.timezone, self._logger)
            self._config = config
            self._plan = None
            alive_names = {schedule.name for schedule in config.schedules}
            self._handled = {name: moment for name, moment in self._handled.items() if name in alive_names}
        self._wakeup.set()
        self._logger.info(
            f'[scheduled_restart] 配置已重载：{len(config.schedules)} 个计划，'
            f'{len(config.warnings)} 条警告，{len(config.errors)} 条错误'
        )

    def get_plan(self) -> Optional[RestartPlan]:
        with self._lock:
            return self._plan

    def next_restart(self) -> Optional[Tuple[Schedule, datetime, float]]:
        """返回 (计划, 重启时刻, 剩余秒数)"""
        with self._lock:
            plan = self._plan
            if plan is None:
                return None
            now = self._clock.now()
            return plan.schedule, plan.restart_time, plan.remaining_seconds(now)

    # ------------------------------------------------------------ 计划查找

    def get_schedule(self, token: str) -> Optional[Schedule]:
        """按序号或名称取计划（供指令使用），找不到返回 None"""
        return self.find_schedule(token)[0]

    def find_schedule(self, token: str) -> Tuple[Optional[Schedule], Optional[str]]:
        """按「配置文件里的序号」或「计划名」查找计划

        序号是 ``!!srestart list`` 里显示的 1 起编号，等价于配置文件 ``schedules``
        数组下标 +1；即使用户把某条计划的 cron 写错导致它被跳过，序号也不会错位。

        :return: (计划, 错误信息)；找到时错误信息为 None
        """
        token = str(token).strip()
        if token == '':
            return None, '请提供计划序号（!!srestart list 查看）或计划名'

        if token.isdigit():
            wanted = int(token)
            for schedule in self._config.schedules:
                if schedule.display_index == wanted:
                    return schedule, None
            if wanted <= 0:
                return None, f'序号必须从 1 开始，收到 {wanted}'
            return None, f'第 {wanted} 项不是有效的重启计划（可能 cron 写错了）；用 !!srestart list 查看'

        for schedule in self._config.schedules:
            if schedule.name == token:
                return schedule, None
        return None, f'找不到序号或名称为 {token!r} 的重启计划；用 !!srestart list 查看序号'

    def schedule_tokens(self) -> List[str]:
        """给指令补全用的候选（序号优先，后面跟计划名）"""
        with self._lock:
            names = [schedule.name for schedule in self._config.schedules]
            return [str(index + 1) for index in range(len(self._config.schedules))] + names

    def list_schedules(self) -> List[Dict[str, Any]]:
        now = self._clock.now()
        with self._lock:
            plan = self._plan
            result: List[Dict[str, Any]] = []
            for schedule in self._config.schedules:
                enabled = schedule.enabled
                entry: Dict[str, Any] = {
                    'index': schedule.display_index,
                    'file_index': schedule.source_index,
                    'name': schedule.name,
                    'enabled': enabled,
                    'cron': schedule.cron_expression,
                    'description': schedule.cron.describe(),
                    'restart_method': schedule.restart_method,
                    'restart_delay_seconds': schedule.restart_delay_seconds,
                    'notifications': len(self._config.notifications_for(schedule)),
                    'next': None,
                    'remaining': None,
                }
                if enabled:
                    if plan is not None and plan.schedule.name == schedule.name:
                        next_time = plan.restart_time
                    else:
                        next_time = self._next_restart_time(schedule, now)
                    if next_time is not None:
                        entry['next'] = next_time
                        entry['remaining'] = max(0.0, (next_time - now).total_seconds())
                result.append(entry)
            return result

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            now = self._clock.now()
            plan = self._plan
            return {
                'enabled': self._config.enabled,
                'running': self.is_running(),
                'restarting': self._restarting,
                'timezone': self._clock.timezone_name(),
                'check_interval_seconds': self._config.check_interval_seconds,
                'schedule_count': len(self._config.schedules),
                'enabled_schedule_count': len([
                    item for item in self._config.schedules if item.enabled
                ]),
                'plan': None if plan is None else {
                    'schedule': plan.schedule.name,
                    'restart_time': plan.restart_time,
                    'remaining': plan.remaining_seconds(now),
                },
                'warnings': list(self._config.warnings),
                'errors': list(self._config.errors),
            }

    def cancel(self, *, name: Optional[str] = None) -> Optional[RestartPlan]:
        """取消当前待执行的重启；``name`` 指定时只取消匹配的计划"""
        with self._lock:
            plan = self._plan
            if plan is None:
                return None
            if name is not None and plan.schedule.name != name:
                return None
            self._plan = None
            self._handled[plan.schedule.name] = plan.restart_time
        self._wakeup.set()
        self._logger.info(
            f'[scheduled_restart] 已取消 {plan.schedule.name} 于 '
            f'{plan.restart_time.strftime("%Y-%m-%d %H:%M:%S")} 的重启'
        )
        return plan

    def start_notification_test(self, schedule: Schedule, reply: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        """发送某个计划的所有提醒（不会真的重启），用于预览效果"""
        notifications = [item for item in self._config.notifications_for(schedule) if item.enabled]
        if not notifications:
            return False, f'计划 {schedule.name} 没有启用中的提醒'
        notifications.sort(key=lambda item: -item.advance_seconds)

        def worker() -> None:
            now = self._clock.now()
            suffix = '（测试）'
            for index, notification in enumerate(notifications):
                if self._stop_event.is_set():
                    break
                try:
                    context = build_context(
                        schedule_name=schedule.name + suffix,
                        cron_expression=schedule.cron_expression,
                        restart_time=now + timedelta(seconds=notification.advance_seconds),
                        now=now,
                        index=index + 1,
                        total=len(notifications),
                    )
                    self._notifier.send_notification(notification, context)
                except Exception:
                    self._logger.exception('[scheduled_restart] 发送测试提醒失败')
                if index + 1 < len(notifications):
                    time_module.sleep(1.2)
            message = f'已发送 {len(notifications)} 条测试提醒（不会真正重启）'
            self._logger.info(f'[scheduled_restart] {schedule.name}：{message}')
            if reply is not None:
                try:
                    reply(message)
                except Exception:  # pragma: no cover
                    pass

        threading.Thread(target=worker, name='ScheduledRestart-Test', daemon=True).start()
        return True, f'正在按提前量依次发送 {len(notifications)} 条测试提醒…'

    def on_player_joined(self, player: str) -> None:
        """玩家进服时私聊提醒当前重启倒计时"""
        if not self._config.notify_on_join:
            return
        with self._lock:
            plan = self._plan
            if plan is None:
                plan = self._compute_plan(self._clock.now())
            if plan is None:
                return
            now = self._clock.now()
            remaining = plan.remaining_seconds(now)
            if remaining <= 0:
                return
            context = build_context(
                schedule_name=plan.schedule.name,
                cron_expression=plan.schedule.cron_expression,
                restart_time=plan.restart_time,
                now=now,
            )
            message = render(self._config.join_message, context)
        try:
            self._notifier.tell_chat(player, message)
        except Exception:
            self._logger.exception('[scheduled_restart] 向加入的玩家发送提醒失败')

    @staticmethod
    def format_remaining(seconds: float) -> str:
        return format_duration_cn(seconds)
