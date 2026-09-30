"""调度核心（计划计算 / 提醒触发 / 重启执行）的测试

时间完全由 :class:`~fakes.FakeClock` 控制，直接驱动 ``_tick()``，
所以这些测试既不依赖真实时间也不需要等待真实线程（只有执行重启的工作线程会被等待）。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta

import pytest

from scheduled_restart.config import DEFAULT_CONFIG, Config
from scheduled_restart.scheduler import RestartScheduler

from fakes import FakeClock, FakeServer, plain_text, read_history_lines, wait_until

DAILY = '0 4 * * *'


def daily_config(notifications=None, *, global_overrides=None, **schedule_overrides) -> dict:
    schedule = {
        'name': '每日重启',
        'enabled': True,
        'cron': DAILY,
        'use_default_notifications': False,
        'notifications': [
            {'advance_seconds': 300, 'type': 'chat', 'message': 'a-{remaining}'},
            {'advance_seconds': 60, 'type': 'chat', 'message': 'b-{remaining}'},
            {'advance_seconds': 0, 'type': 'chat', 'message': 'c-{remaining}'},
        ] if notifications is None else notifications,
    }
    schedule.update(schedule_overrides)
    config = {
        'enabled': True,
        'schedules': [schedule],
        'default_notifications': copy.deepcopy(DEFAULT_CONFIG['default_notifications']),
    }
    config.update(global_overrides or {})
    return config


def make_scheduler(raw_config: dict, moment: datetime, *, server: FakeServer = None):
    server = server if server is not None else FakeServer(raw_config)
    config = Config.from_raw(raw_config)
    assert config.errors == [], config.errors
    clock = FakeClock(moment)
    scheduler = RestartScheduler(server, config, clock=clock)
    return server, config, clock, scheduler


# --------------------------------------------------------------------------- #
#                                  计划计算                                     #
# --------------------------------------------------------------------------- #


def test_plan_is_computed_on_first_tick():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))
    assert scheduler.next_restart() is None

    scheduler._tick()
    upcoming = scheduler.next_restart()
    assert upcoming is not None
    schedule, restart_time, remaining = upcoming
    assert schedule.name == '每日重启'
    assert restart_time == datetime(2026, 1, 1, 4, 0)
    assert remaining == 3600


def test_plan_respects_restart_delay():
    server, config, clock, scheduler = make_scheduler(
        daily_config(restart_delay_seconds=90), datetime(2026, 1, 1, 3, 0)
    )
    scheduler._tick()
    _, restart_time, _ = scheduler.next_restart()
    assert restart_time == datetime(2026, 1, 1, 4, 1, 30)
    # 提醒以真正的重启时刻为基准
    plan = scheduler.get_plan()
    fire_times = sorted(item.fire_time for item in plan.notifications)
    assert fire_times[-1] == datetime(2026, 1, 1, 4, 1, 30)


def test_global_disable_stops_scheduling():
    server, config, clock, scheduler = make_scheduler(
        daily_config(global_overrides={'enabled': False}), datetime(2026, 1, 1, 3, 0)
    )
    scheduler._tick()
    assert scheduler.next_restart() is None


# --------------------------------------------------------------------------- #
#                                  提醒触发                                     #
# --------------------------------------------------------------------------- #


def test_notifications_fire_at_configured_advance():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))

    scheduler._tick()
    assert server.said == []

    clock.set(datetime(2026, 1, 1, 3, 54, 59))
    scheduler._tick()
    assert server.said == []

    clock.set(datetime(2026, 1, 1, 3, 55, 0))
    scheduler._tick()
    assert [plain_text(item) for item in server.said] == ['a-5分']

    clock.set(datetime(2026, 1, 1, 3, 59, 0))
    scheduler._tick()
    assert [plain_text(item) for item in server.said] == ['a-5分', 'b-1分']

    clock.set(datetime(2026, 1, 1, 4, 0, 0))
    scheduler._tick()
    assert [plain_text(item) for item in server.said] == ['a-5分', 'b-1分', 'c-0秒']

    assert wait_until(lambda: not scheduler._restarting)
    assert server.restart_calls == 1


def test_missed_notifications_are_skipped():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 59, 30))
    scheduler._tick()
    plan = scheduler.get_plan()
    assert [item.skipped for item in plan.notifications] == [True, True, False]
    assert server.said == []

    clock.set(datetime(2026, 1, 1, 4, 0, 0))
    scheduler._tick()
    assert [plain_text(item) for item in server.said] == ['c-0秒']
    assert wait_until(lambda: not scheduler._restarting)


def test_missed_notifications_can_be_forced():
    server, config, clock, scheduler = make_scheduler(
        daily_config(global_overrides={'skip_missed_notifications': False}),
        datetime(2026, 1, 1, 3, 59, 30),
    )
    scheduler._tick()
    # 已过期的提醒会立刻补发（剩余时间按当前时刻计算）
    assert [plain_text(item) for item in server.said] == ['a-30秒', 'b-30秒']


def test_disabled_notification_is_not_sent():
    notifications = [
        {'advance_seconds': 60, 'type': 'chat', 'message': 'x', 'enabled': False},
        {'advance_seconds': 0, 'type': 'chat', 'message': 'y'},
    ]
    server, config, clock, scheduler = make_scheduler(daily_config(notifications), datetime(2026, 1, 1, 3, 59, 0))
    scheduler._tick()
    assert len(scheduler.get_plan().notifications) == 1
    assert server.said == []


def test_disabled_schedule_is_not_scheduled():
    server, config, clock, scheduler = make_scheduler(daily_config(enabled=False), datetime(2026, 1, 1, 3, 0))
    scheduler._tick()
    assert scheduler.next_restart() is None


# --------------------------------------------------------------------------- #
#                                  重启执行                                     #
# --------------------------------------------------------------------------- #


def test_restart_method_mcdr_restart():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))
    assert scheduler._execute_restart(config.schedules[0], manual=False, actor='') is True
    assert server.restart_calls == 1
    assert server.stop_calls == 0 and server.stop_exit_calls == 0


@pytest.mark.parametrize('method,attribute', [
    ('stop', 'stop_calls'),
    ('stop_exit', 'stop_exit_calls'),
])
def test_restart_method_stop_variants(method, attribute):
    server, config, clock, scheduler = make_scheduler(
        daily_config(restart_method=method), datetime(2026, 1, 1, 3, 0)
    )
    assert scheduler._execute_restart(config.schedules[0], manual=False, actor='') is True
    assert getattr(server, attribute) == 1


def test_restart_method_custom():
    server, config, clock, scheduler = make_scheduler(
        daily_config(restart_method='custom', custom_command='stop'), datetime(2026, 1, 1, 3, 0)
    )
    assert scheduler._execute_restart(config.schedules[0], manual=False, actor='') is True
    assert server.executed == ['stop']
    assert server.start_calls == 0


def test_restart_method_custom_with_auto_start():
    server, config, clock, scheduler = make_scheduler(
        daily_config(restart_method='custom', custom_command='stop', custom_auto_start=True),
        datetime(2026, 1, 1, 3, 0),
    )
    assert scheduler._execute_restart(config.schedules[0], manual=False, actor='') is True
    assert server.executed == ['stop']
    assert server.wait_for_start_calls == 1
    assert server.start_calls == 1


def test_restart_method_none_only_notifies():
    server, config, clock, scheduler = make_scheduler(
        daily_config(restart_method='none'), datetime(2026, 1, 1, 3, 0)
    )
    assert scheduler._execute_restart(config.schedules[0], manual=False, actor='') is True
    assert server.restart_calls == 0 and server.stop_calls == 0 and server.executed == []


def test_restart_is_skipped_when_server_not_running():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))
    server.running = False
    assert scheduler._execute_restart(config.schedules[0], manual=False, actor='') is False
    assert server.restart_calls == 0


def test_notifications_are_skipped_when_server_not_running():
    """服务器没运行时不应该记「已发送提醒」"""
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 55))
    server.running = False
    scheduler._tick()
    assert server.said == []
    assert server.executed == []
    assert scheduler.get_plan() is not None      # 计划仍然保留，等服务器起来后照常执行


def test_kick_players_before_restart():
    server, config, clock, scheduler = make_scheduler(
        daily_config(kick_players=True, kick_message='重启了，稍后再来'), datetime(2026, 1, 1, 3, 0)
    )
    scheduler._execute_restart(config.schedules[0], manual=False, actor='')
    assert server.executed == ['kick @a 重启了，稍后再来']
    assert server.restart_calls == 1


def test_every_minute_schedule_does_not_repeat_same_occurrence():
    server, config, clock, scheduler = make_scheduler(
        daily_config(cron='* * * * *', notifications=[]), datetime(2026, 1, 1, 10, 0, 30)
    )
    scheduler._tick()
    assert scheduler.next_restart()[1] == datetime(2026, 1, 1, 10, 1)

    clock.set(datetime(2026, 1, 1, 10, 1, 0))
    scheduler._tick()
    assert wait_until(lambda: not scheduler._restarting)
    assert server.restart_calls == 1

    # 同一分钟内重复 tick 不会再次触发
    scheduler._tick()
    scheduler._tick()
    assert server.restart_calls == 1
    assert scheduler.next_restart()[1] == datetime(2026, 1, 1, 10, 2)


# --------------------------------------------------------------------------- #
#                              取消 / 手动 / 开关                               #
# --------------------------------------------------------------------------- #


def test_cancel_current_plan():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))
    scheduler._tick()
    plan = scheduler.cancel()
    assert plan is not None and plan.schedule.name == '每日重启'
    assert scheduler.cancel() is None

    # 被取消的那一次不会再触发；下一 tick 计划顺延到第二天
    clock.set(datetime(2026, 1, 1, 4, 0, 0))
    scheduler._tick()
    assert server.restart_calls == 0
    assert scheduler.next_restart()[1] == datetime(2026, 1, 2, 4, 0)


def test_cancel_with_mismatched_name_does_nothing():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))
    scheduler._tick()
    assert scheduler.cancel(name='不存在的计划') is None
    assert scheduler.next_restart() is not None


def test_find_schedule_by_index_and_name():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))

    found, error = scheduler.find_schedule('1')
    assert error is None and found is config.schedules[0]

    found, error = scheduler.find_schedule('每日重启')
    assert error is None and found.display_index == 1

    found, error = scheduler.find_schedule(' 1 ')
    assert error is None and found is not None

    found, error = scheduler.find_schedule('2')
    assert found is None and '第 2 项' in error

    found, error = scheduler.find_schedule('不存在')
    assert found is None and '找不到' in error

    found, error = scheduler.find_schedule('')
    assert found is None and '请提供' in error

    found, error = scheduler.find_schedule('0')
    assert found is None and '从 1 开始' in error


def test_display_index_keeps_alignment_when_a_schedule_is_broken():
    """中间某条计划 cron 写错被跳过时，序号仍然对应配置文件里的位置"""
    raw = daily_config()
    raw['schedules'] = [
        {'name': '好的1', 'cron': '0 4 * * *', 'enabled': True},
        {'name': '坏的', 'cron': '99 * * * *', 'enabled': True},
        {'name': '好的2', 'cron': '0 5 * * *', 'enabled': True},
    ]
    server = FakeServer(raw)
    config = Config.from_raw(raw)
    assert len(config.errors) == 1                      # 「坏的」被跳过
    scheduler = RestartScheduler(server, config, clock=FakeClock(datetime(2026, 1, 1, 3, 0)))
    assert [schedule.name for schedule in config.schedules] == ['好的1', '好的2']

    entries = scheduler.list_schedules()
    assert [entry['index'] for entry in entries] == [1, 3]
    assert [entry['name'] for entry in entries] == ['好的1', '好的2']

    found, error = scheduler.find_schedule('3')
    assert error is None and found.name == '好的2'
    found, error = scheduler.find_schedule('2')
    assert found is None and '第 2 项' in error


def test_reload_rebuilds_plan():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))
    scheduler._tick()
    assert scheduler.next_restart()[1] == datetime(2026, 1, 1, 4, 0)

    new_raw = daily_config(cron='0 5 * * *')
    new_config = Config.from_raw(new_raw)
    scheduler.reload(new_config, keep_clock=True)
    assert scheduler.next_restart() is None
    scheduler._tick()
    assert scheduler.next_restart()[1] == datetime(2026, 1, 1, 5, 0)


# --------------------------------------------------------------------------- #
#                                   历史记录                                    #
# --------------------------------------------------------------------------- #


def test_history_is_written_and_read():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 4, 0))
    scheduler._execute_restart(config.schedules[0], manual=False, actor='')
    lines = read_history_lines(server)
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record['schedule'] == '每日重启'
    assert record['cron'] == DAILY
    assert record['method'] == 'mcdr_restart'
    assert record['manual'] is False
    assert scheduler.read_history(5)[0]['schedule'] == '每日重启'


def test_history_can_be_disabled():
    server, config, clock, scheduler = make_scheduler(
        daily_config(global_overrides={'log_history': False}), datetime(2026, 1, 1, 4, 0)
    )
    scheduler._execute_restart(config.schedules[0], manual=False, actor='')
    assert read_history_lines(server) == []
    assert scheduler.read_history(5) == []


def test_history_reader_tolerates_broken_lines():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 4, 0))
    scheduler._execute_restart(config.schedules[0], manual=False, actor='')
    with open(scheduler.history_path, 'a', encoding='utf8') as file_handler:
        file_handler.write('这不是 JSON\n')
    assert len(scheduler.read_history(10)) == 1


# --------------------------------------------------------------------------- #
#                                 进服提醒 / 状态                                #
# --------------------------------------------------------------------------- #


def test_notify_on_join():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 30))
    scheduler._tick()
    scheduler.on_player_joined('Steve')
    assert len(server.told) == 1
    player, message = server.told[0]
    assert player == 'Steve'
    assert '30分' in plain_text(message)


def test_notify_on_join_can_be_disabled():
    server, config, clock, scheduler = make_scheduler(
        daily_config(global_overrides={'notify_on_join': False}), datetime(2026, 1, 1, 3, 30)
    )
    scheduler._tick()
    scheduler.on_player_joined('Steve')
    assert server.told == []


def test_notify_on_join_without_plan():
    server, config, clock, scheduler = make_scheduler(
        daily_config(cron='0 4 * * *', enabled=False), datetime(2026, 1, 1, 3, 30)
    )
    scheduler._tick()
    scheduler.on_player_joined('Steve')
    assert server.told == []


def test_list_schedules_and_status():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))
    scheduler._tick()

    entries = scheduler.list_schedules()
    assert len(entries) == 1
    entry = entries[0]
    assert entry['index'] == 1
    assert entry['file_index'] == 0
    assert entry['name'] == '每日重启'
    assert entry['enabled'] is True
    assert entry['cron'] == DAILY
    assert entry['description'] == '每天 04:00'
    assert entry['next'] == datetime(2026, 1, 1, 4, 0)
    assert entry['remaining'] == 3600
    assert entry['notifications'] == 3

    status = scheduler.get_status()
    assert status['enabled'] is True
    assert status['running'] is False          # 没有启动线程，直接驱动 _tick
    assert status['schedule_count'] == 1
    assert status['enabled_schedule_count'] == 1
    assert status['plan']['schedule'] == '每日重启'
    assert status['warnings'] == [] and status['errors'] == []


def test_read_history_without_data_folder():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))
    scheduler._data_folder = None
    scheduler._execute_restart(config.schedules[0], manual=False, actor='')
    assert scheduler.read_history(5) == []


def test_test_notifications_preview():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))
    replies = []
    ok, message = scheduler.start_notification_test(config.schedules[0], reply=replies.append)
    assert ok is True and '3' in message
    assert wait_until(lambda: len(server.said) == 3, timeout=5)
    assert [plain_text(item) for item in server.said] == ['a-5分', 'b-1分', 'c-0秒']
    assert wait_until(lambda: len(replies) == 1, timeout=5)
    assert server.restart_calls == 0


def test_test_notifications_without_enabled_notification():
    server, config, clock, scheduler = make_scheduler(
        daily_config([{'advance_seconds': 60, 'type': 'chat', 'message': 'x', 'enabled': False}]),
        datetime(2026, 1, 1, 3, 0),
    )
    ok, message = scheduler.start_notification_test(config.schedules[0])
    assert ok is False and '没有启用中的提醒' in message


def test_stop_and_start_thread():
    server, config, clock, scheduler = make_scheduler(daily_config(), datetime(2026, 1, 1, 3, 0))
    scheduler.start()
    assert scheduler.is_running() is True
    scheduler.stop()
    assert scheduler.is_running() is False


# --------------------------------------------------------------------------- #
#                                时间格式化                                     #
# --------------------------------------------------------------------------- #


def test_format_remaining():
    assert RestartScheduler.format_remaining(0) == '0秒'
    assert RestartScheduler.format_remaining(3661) == '1小时1分1秒'
