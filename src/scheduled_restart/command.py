"""``!!srestart`` 指令树。

指令一览（``!!srestart help`` 也可查看）：

* ``!!srestart list``                    所有计划：序号、cron、中文描述、下次重启时间（helper）
* ``!!srestart next``                    下一次重启倒计时（helper）
* ``!!srestart status``                  插件状态与配置问题（helper）
* ``!!srestart history [条数]``           最近的重启记录（helper）
* ``!!srestart reload``                  热重载配置文件（admin）
* ``!!srestart cancel``                  取消当前待执行的重启（admin）
* ``!!srestart test <序号|计划名>``        只发送该计划的提醒，用于预览效果（admin）
* ``!!srestart enable [<序号|计划名>|all]`` 启用计划并写入配置文件（admin）
* ``!!srestart disable [<序号|计划名>|all]`` 禁用计划并写入配置文件（admin）
* ``!!srestart add <计划名> <cron>``       新建计划（默认启用、使用默认提醒组）（admin）
* ``!!srestart remove <序号|计划名>``      删除计划（admin）

计划统一用 ``!!srestart list`` 里显示的**序号**指代（序号等价于配置文件 ``schedules``
数组下标 +1，即使某条计划写错被跳过也不会错位），也兼容直接写计划名。
"""

from __future__ import annotations

import functools
import inspect
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from mcdreforged.api.command import GreedyText, Integer, Literal, QuotableText
from mcdreforged.api.rtext import RColor, RText
from mcdreforged.api.types import CommandSource, PermissionLevel

from .config import Config, Schedule
from .config_store import ConfigFileEditor
from .scheduler import RestartScheduler

__all__ = ['register_commands', 'COMMAND_PREFIX']

COMMAND_PREFIX = '!!srestart'

USER_PERMISSION = PermissionLevel.USER
VIEW_PERMISSION = PermissionLevel.HELPER
ADMIN_PERMISSION = PermissionLevel.ADMIN

#: 插件日志里用的名字
LOGGER_NAME = 'scheduled_restart'


def _gray(text: str) -> RText:
    return RText(text, RColor.gray)


def _format_time(moment: Optional[datetime]) -> str:
    return '—' if moment is None else moment.strftime('%Y-%m-%d %H:%M:%S')


def _format_remaining(seconds: Optional[float]) -> str:
    if seconds is None:
        return '—'
    return RestartScheduler.format_remaining(seconds)


def _guard(server) -> Callable:
    """包住指令回调，避免任何异常直接抛到 MCDR 的命令处理里

    .. note::
        MCDR 会根据回调的形参个数来裁剪传入的参数（``inspect.getfullargspec``），
        而它不会跟随 ``functools.wraps`` 的 ``__wrapped__``，
        所以包装器必须显式声明 ``(source, context)`` 两个形参。
    """

    def decorator(func):
        arity = len(inspect.getfullargspec(func).args)

        @functools.wraps(func)
        def wrapper(source, context=None, *args, **kwargs):
            try:
                if arity <= 1:
                    return func(source)
                return func(source, context)
            except Exception as e:
                server.logger.exception(f'[{LOGGER_NAME}] 执行指令时出现异常')
                source.reply(RText(f'[定时重启] 执行失败：{e}', RColor.red))

        return wrapper

    return decorator


def register_commands(
        server,
        scheduler: RestartScheduler,
        *,
        config_provider: Callable[[], Config],
        apply_config: Callable[[], Tuple[bool, str]],
        editor: ConfigFileEditor,
        command_alias: Optional[str] = None,
) -> None:
    """注册 ``!!srestart`` 指令树与帮助信息

    :param config_provider: 取当前生效配置
    :param apply_config: 重新读取配置文件并让调度生效，返回 (是否成功, 说明)
    :param editor: 配置文件读改写（enable / disable / add / remove 用）
    :param command_alias: 可选的自定义简化指令前缀，如 ``!!sr``
    """

    guard = _guard(server)

    def target_node(name: str = 'target', *, include_all: bool = False) -> QuotableText:
        """序号 / 计划名参数节点（带 Tab 补全）"""

        def suggestions(source, context) -> List[str]:
            tokens = scheduler.schedule_tokens()
            if include_all:
                tokens.append('all')
            return tokens

        return QuotableText(name).suggests(suggestions)

    def resolve_or_reply(source, token: str) -> Optional[Schedule]:
        schedule, error = scheduler.find_schedule(token)
        if schedule is None:
            source.reply(RText(f'[定时重启] {error}', RColor.red))
        return schedule

    def reply_result(source, ok: bool, message: str) -> None:
        source.reply(RText(f'[定时重启] {message}', RColor.green if ok else RColor.red))

    def write_and_apply(source, message: str) -> None:
        """把修改后的配置写入内存并回复结果"""
        applied, apply_message = apply_config()
        if applied:
            reply_result(source, True, f'{message}；{apply_message}')
        else:
            reply_result(source, False, f'{message}，但重新加载配置失败：{apply_message}')

    # ---------------------------------------------------------------- 查看类

    @guard
    def show_help(source):
        lines = [
            RText(f'===== 定时重启 {COMMAND_PREFIX} =====', RColor.aqua),
            RText(f'{COMMAND_PREFIX} list', RColor.yellow) + _gray('  查看所有重启计划与下次重启时间'),
            RText(f'{COMMAND_PREFIX} next', RColor.yellow) + _gray('  查看下一次重启倒计时'),
            RText(f'{COMMAND_PREFIX} status', RColor.yellow) + _gray('  查看插件状态与配置问题'),
            RText(f'{COMMAND_PREFIX} history [条数]', RColor.yellow) + _gray('  查看最近的重启记录'),
            RText(f'{COMMAND_PREFIX} test <序号|计划名>', RColor.yellow) + _gray('  只发送该计划的提醒，用于预览'),
            RText(f'{COMMAND_PREFIX} enable [<序号|计划名>|all]', RColor.yellow)
            + _gray('   启用计划（写入配置文件）'),
            RText(f'{COMMAND_PREFIX} disable [<序号|计划名>|all]', RColor.yellow)
            + _gray('  禁用计划（写入配置文件）'),
            RText(f'{COMMAND_PREFIX} add <计划名> <cron>', RColor.yellow)
            + _gray('  新建计划，如 add 每日重启 0 4 * * *'),
            RText(f'{COMMAND_PREFIX} remove <序号|计划名>', RColor.yellow) + _gray('  删除计划'),
            RText(f'{COMMAND_PREFIX} reload', RColor.yellow) + _gray('  重新读取配置文件'),
            RText(f'{COMMAND_PREFIX} cancel', RColor.yellow) + _gray('  取消当前待执行的重启'),
            _gray('计划用 list 里的序号指代（也可以写计划名）；立刻重启服务器请用 !!MCDR server restart'),
        ]
        if command_alias:
            lines.append(_gray(f'简化指令：{command_alias} 与 {COMMAND_PREFIX} 等价'))
        for line in lines:
            source.reply(line)

    @guard
    def show_list(source):
        entries = scheduler.list_schedules()
        config = config_provider()
        head = (
            f'===== 定时重启计划（{len(entries)} 个，总开关 {"开启" if config.enabled else "关闭"}）====='
        )
        source.reply(RText(head, RColor.aqua))
        if not entries:
            source.reply(_gray(f'（没有配置任何计划，可用 {COMMAND_PREFIX} add <计划名> <cron> 新建）'))
            return
        for entry in entries:
            status = RText('已启用', RColor.green) if entry['enabled'] else RText('已禁用', RColor.red)
            name = RText(f'[#{entry["index"]}] {entry["name"]} ', RColor.yellow) + status
            source.reply(name)
            source.reply(_gray('    cron: ') + RText(entry['cron'], RColor.white)
                         + _gray(f'（{entry["description"]}）'))
            source.reply(_gray('    下次: ') + RText(_format_time(entry['next']), RColor.white)
                         + _gray('（剩余 ') + RText(_format_remaining(entry['remaining']), RColor.aqua) + _gray('）'))
            source.reply(_gray('    方式: ') + RText(entry['restart_method'], RColor.white)
                         + _gray(f'，提醒 {entry["notifications"]} 条'))
        if config.errors:
            source.reply(RText(f'注意：有 {len(config.errors)} 条计划因为配置写错被跳过了，'
                               f'用 {COMMAND_PREFIX} status 查看', RColor.yellow))

    @guard
    def show_next(source):
        upcoming = scheduler.next_restart()
        if upcoming is None:
            source.reply(RText('[定时重启] 当前没有待执行的重启计划', RColor.gray))
            return
        schedule, restart_time, remaining = upcoming
        source.reply(
            RText('[定时重启] 下一次重启：', RColor.aqua)
            + RText(f'#{schedule.display_index} {schedule.name}', RColor.yellow)
            + _gray(' 于 ')
            + RText(_format_time(restart_time), RColor.white)
            + _gray('（剩余 ')
            + RText(_format_remaining(remaining), RColor.aqua)
            + _gray('）')
        )

    @guard
    def show_status(source):
        status = scheduler.get_status()
        source.reply(RText('===== 定时重启状态 =====', RColor.aqua))
        source.reply(_gray('总开关: ') + RText('开启' if status['enabled'] else '关闭',
                                              RColor.green if status['enabled'] else RColor.red)
                     + _gray('，调度线程: ') + RText('运行中' if status['running'] else '未运行',
                                                    RColor.green if status['running'] else RColor.red))
        source.reply(_gray('时区: ') + RText(status['timezone'] or '本地时间', RColor.white)
                     + _gray('，检查间隔: ') + RText(f'{status["check_interval_seconds"]:g} 秒', RColor.white))
        source.reply(_gray('计划: ') + RText(f'{status["schedule_count"]} 个（启用 {status["enabled_schedule_count"]} 个）',
                                            RColor.white))
        plan = status['plan']
        if plan is None:
            source.reply(_gray('当前计划: 无'))
        else:
            source.reply(_gray('当前计划: ') + RText(plan['schedule'], RColor.yellow)
                         + _gray(' -> ')
                         + RText(_format_time(plan['restart_time']), RColor.white)
                         + _gray('（剩余 ')
                         + RText(_format_remaining(plan['remaining']), RColor.aqua)
                         + _gray('）'))
        if status['restarting']:
            source.reply(RText('正在执行重启流程…', RColor.yellow))
        for warning in status['warnings'][:5]:
            source.reply(RText('配置警告: ', RColor.yellow) + _gray(warning))
        if len(status['warnings']) > 5:
            source.reply(_gray(f'…… 还有 {len(status["warnings"]) - 5} 条配置警告，详见控制台日志'))
        for error in status['errors'][:5]:
            source.reply(RText('配置错误: ', RColor.red) + _gray(error))
        if status['errors']:
            source.reply(_gray('存在配置错误时插件不会回写配置文件，请手动修正后执行 reload'))

    @guard
    def show_history(source, context):
        count = int(context.get('count', 10))
        records = scheduler.read_history(count)
        source.reply(RText(f'===== 最近 {len(records)} 条重启记录 =====', RColor.aqua))
        if not records:
            source.reply(_gray('（暂无记录，重启记录保存在 config/scheduled_restart/history.jsonl）'))
            return
        for record in reversed(records):
            source.reply(
                RText(str(record.get('time', '?')), RColor.white)
                + _gray(' | ')
                + RText(str(record.get('schedule', '?')), RColor.yellow)
                + _gray(' | ')
                + RText(str(record.get('method', '?')), RColor.white)
                + _gray(' | ')
                + RText('手动' if record.get('manual') else '自动', RColor.aqua)
                + (_gray(f'（{record.get("actor")}）') if record.get('actor') else RText(''))
            )

    # ---------------------------------------------------------------- 操作类

    @guard
    def do_reload(source):
        ok, message = apply_config()
        reply_result(source, ok, message)

    @guard
    def do_cancel(source):
        plan = scheduler.cancel()
        if plan is None:
            source.reply(RText('[定时重启] 当前没有待执行的重启，无需取消', RColor.gray))
            return
        source.reply(RText('[定时重启] 已取消计划 ', RColor.green)
                     + RText(f'#{plan.schedule.display_index} {plan.schedule.name}', RColor.yellow)
                     + _gray(f'（原定 {_format_time(plan.restart_time)}）'))

    @guard
    def do_test(source, context):
        schedule = resolve_or_reply(source, context['target'])
        if schedule is None:
            return

        def reply(text: str) -> None:
            source.reply(RText(f'[定时重启] {text}', RColor.green))

        ok, message = scheduler.start_notification_test(schedule, reply=reply)
        reply_result(source, ok, message)

    def toggle(source, context, enabled: bool) -> None:
        token = str(context.get('target', '') or '').strip()
        if token == '' or token.lower() == 'all':
            ok, message = editor.set_all_enabled(enabled)
        elif token.isdigit():
            # 数字直接按配置文件序号处理，这样连 cron 写错的计划也能开关
            ok, message = editor.set_enabled(int(token) - 1, enabled)
        else:
            schedule, error = scheduler.find_schedule(token)
            if schedule is None:
                reply_result(source, False, error or '找不到该计划')
                return
            ok, message = editor.set_enabled(schedule.source_index, enabled)
        if not ok:
            reply_result(source, False, message)
            return
        write_and_apply(source, message)

    @guard
    def do_enable(source, context):
        toggle(source, context, True)

    @guard
    def do_disable(source, context):
        toggle(source, context, False)

    @guard
    def do_add(source, context):
        name = context['name']
        cron_expression = context['cron']
        ok, message = editor.add_schedule(name, cron_expression)
        if not ok:
            reply_result(source, False, message)
            return
        write_and_apply(source, message)

    @guard
    def do_remove(source, context):
        token = str(context['target']).strip()
        if token.lower() == 'all':
            reply_result(source, False, '不支持一次删除全部计划，请逐个指定序号')
            return
        schedule = resolve_or_reply(source, token)
        if schedule is None:
            return
        ok, message = editor.remove_schedule(schedule.source_index)
        if not ok:
            reply_result(source, False, message)
            return
        write_and_apply(source, message)

    # ---------------------------------------------------------------- 组装树

    def _failure_message_forbidden(needed: str, source) -> str:
        player = getattr(source, 'player', None)
        if player is not None:
            return (
                f'§c[定时重启] 需要 MCDR {needed} 权限§r；'
                f'Minecraft 的 /op 不等于 MCDR {needed}，'
                f'请让服主在控制台执行 §7!!MCDR permission set {player} {needed}§r'
            )
        return f'§c[定时重启] 需要 {needed} 权限§r'

    user = lambda src: src.has_permission(USER_PERMISSION)  # noqa: E731
    view = lambda src: src.has_permission(VIEW_PERMISSION)  # noqa: E731
    admin = lambda src: src.has_permission(ADMIN_PERMISSION)  # noqa: E731

    def build_tree(prefix: str):
        root = Literal(prefix)
        root.runs(show_help)

        root.then(Literal('help').runs(show_help))
        root.then(Literal('list').requires(user).runs(show_list))
        root.then(Literal('next').requires(user).runs(show_next))
        root.then(Literal('status').requires(view).runs(show_status))

        history_node = Literal('history').requires(view)
        history_node.runs(lambda src, ctx: show_history(src, {'count': 10}))
        history_node.then(Integer('count').at_min(1).at_max(200).runs(show_history))
        root.then(history_node)

        root.then(
            Literal('reload')
            .requires(admin, lambda src, ctx: _failure_message_forbidden('admin', src))
            .runs(do_reload)
        )
        root.then(
            Literal('cancel')
            .requires(admin, lambda src, ctx: _failure_message_forbidden('admin', src))
            .runs(do_cancel)
        )
        root.then(
            Literal('test')
            .requires(admin, lambda src, ctx: _failure_message_forbidden('admin', src))
            .then(target_node().runs(do_test))
        )

        enable_node = Literal('enable').requires(admin, lambda src, ctx: _failure_message_forbidden('admin', src))
        enable_node.runs(do_enable)
        enable_node.then(target_node(include_all=True).runs(do_enable))
        root.then(enable_node)

        disable_node = Literal('disable').requires(admin, lambda src, ctx: _failure_message_forbidden('admin', src))
        disable_node.runs(do_disable)
        disable_node.then(target_node(include_all=True).runs(do_disable))
        root.then(disable_node)

        root.then(
            Literal('add')
            .requires(admin, lambda src, ctx: _failure_message_forbidden('admin', src))
            .then(QuotableText('name').then(GreedyText('cron').runs(do_add)))
        )
        root.then(
            Literal('remove')
            .requires(admin, lambda src, ctx: _failure_message_forbidden('admin', src))
            .then(target_node().runs(do_remove))
        )

        return root

    help_message = '定时重启插件：查看/新建/开关/删除重启计划'
    server.register_command(build_tree(COMMAND_PREFIX))
    server.register_help_message(COMMAND_PREFIX, help_message, permission=USER_PERMISSION)
    if command_alias:
        server.register_command(build_tree(command_alias))
        server.register_help_message(command_alias, help_message, permission=USER_PERMISSION)
