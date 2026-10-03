"""``!!srestart`` 指令树测试：用 MCDR 真正的指令解析器执行指令

配置相关指令（enable / disable / add / remove）会真的读写数据目录里的 ``config.json``，
所以这里用带文件的假服务器，断言落在文件上。
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

import pytest

from mcdreforged.api.command import CommandError, RequirementNotMet
from mcdreforged.api.types import PermissionLevel

from scheduled_restart.command import COMMAND_PREFIX, register_commands
from scheduled_restart.config import DEFAULT_CONFIG, Config
from scheduled_restart.config_store import ConfigFileEditor
from scheduled_restart.scheduler import RestartScheduler

from fakes import FakeClock, FakeCommandSource, FakeServer, run_command, wait_until

DAILY = '0 4 * * *'


def make_config() -> dict:
    return {
        'enabled': True,
        'default_notifications': copy.deepcopy(DEFAULT_CONFIG['default_notifications']),
        'schedules': [{
            'name': '每日重启',
            'enabled': True,
            'cron': DAILY,
            'use_default_notifications': False,
            'notifications': [
                {'advance_time': 300, 'type': 'chat', 'message': 'a'},
                {'advance_time': 0, 'type': 'chat', 'message': 'b'},
            ],
        }],
    }


def _make_env(alias: Optional[str] = None):
    server = FakeServer(make_config())
    server.seed_config_file()
    config = Config.from_raw(server.config_raw)
    assert config.errors == []
    holder = {'config': config}
    clock = FakeClock(datetime(2026, 1, 1, 3, 0))
    scheduler = RestartScheduler(server, config, clock=clock)
    editor = ConfigFileEditor(server)
    applied: list = []

    def apply_config():
        applied.append(True)
        new_config = Config.from_raw(server.load_config_simple('config.json'))
        holder['config'] = new_config
        scheduler.reload(new_config, keep_clock=True)
        return True, f'配置已生效（{len(new_config.schedules)} 个计划）'

    register_commands(
        server, scheduler,
        config_provider=lambda: holder['config'],
        apply_config=apply_config,
        editor=editor,
        command_alias=alias,
    )
    return SimpleNamespace(
        server=server, scheduler=scheduler, clock=clock, holder=holder,
        root=server.registered_commands[0], applied=applied, editor=editor,
    )


@pytest.fixture
def env():
    return _make_env()


def run(env, command: str, permission: int = PermissionLevel.OWNER):
    source = FakeCommandSource(permission, server=env.server)
    count = run_command(env.root, source, command)
    return source, count


def file_schedules(env) -> list:
    with open(env.server.config_file_path, encoding='utf8') as file_handler:
        return json.load(file_handler)['schedules']


# --------------------------------------------------------------------------- #
#                                    注册                                       #
# --------------------------------------------------------------------------- #


def test_command_and_help_are_registered(env):
    assert len(env.server.registered_commands) == 1
    assert env.server.help_messages[0][0] == COMMAND_PREFIX
    assert env.server.help_messages[0][2] == PermissionLevel.USER


def test_help(env):
    source, count = run(env, COMMAND_PREFIX)
    assert count == 1
    text = source.text()
    assert '定时重启' in text
    for sub in ('list', 'next', 'status', 'history', 'test', 'enable', 'disable', 'add', 'remove', 'reload', 'cancel'):
        assert f'{COMMAND_PREFIX} {sub}' in text
    assert 'trigger' not in text
    assert '!!MCDR server restart' in text


def test_trigger_is_gone(env):
    """trigger 已按需求删除：立刻重启交给 MCDR 自带的 !!MCDR server restart"""
    with pytest.raises(CommandError):
        run(env, f'{COMMAND_PREFIX} trigger 1')


# --------------------------------------------------------------------------- #
#                                   查看类                                      #
# --------------------------------------------------------------------------- #


def test_list(env):
    env.scheduler._tick()
    source, _ = run(env, f'{COMMAND_PREFIX} list')
    text = source.text()
    assert '[#1] 每日重启' in text
    assert '已启用' in text
    assert DAILY in text
    assert '每天 04:00' in text
    assert '2026-01-01 04:00:00' in text
    assert '提醒 2 条' in text


def test_list_without_schedule(env):
    source, _ = run(env, f'{COMMAND_PREFIX} remove 1')
    assert '已删除计划' in source.text()

    source, _ = run(env, f'{COMMAND_PREFIX} list')
    assert '没有配置任何计划' in source.text()
    assert f'{COMMAND_PREFIX} add' in source.text()


def test_list_mentions_broken_schedules(env):
    raw = json.loads(open(env.server.config_file_path, encoding='utf8').read())
    raw['schedules'].append({'name': '坏的', 'cron': '99 * * * *'})
    with open(env.server.config_file_path, 'w', encoding='utf8') as file_handler:
        json.dump(raw, file_handler, ensure_ascii=False)

    source, _ = run(env, f'{COMMAND_PREFIX} reload')
    source, _ = run(env, f'{COMMAND_PREFIX} list')
    assert '被跳过' in source.text()


def test_next(env):
    env.scheduler._tick()
    source, _ = run(env, f'{COMMAND_PREFIX} next')
    assert '#1 每日重启' in source.text()
    assert '1小时' in source.text()

    env.editor.set_all_enabled(False)
    env.scheduler.reload(Config.from_raw(env.server.load_config_simple('config.json')), keep_clock=True)
    source, _ = run(env, f'{COMMAND_PREFIX} next')
    assert '没有待执行的重启计划' in source.text()


def test_status(env):
    env.scheduler._tick()
    source, _ = run(env, f'{COMMAND_PREFIX} status')
    text = source.text()
    assert '总开关' in text
    assert '计划: 1 个（启用 1 个）' in text


def test_history(env):
    source, _ = run(env, f'{COMMAND_PREFIX} history')
    assert '暂无记录' in source.text()

    env.scheduler._execute_restart(env.scheduler._config.schedules[0], manual=False, actor='')
    source, count = run(env, f'{COMMAND_PREFIX} history 5')
    assert count == 1
    assert '每日重启' in source.text()
    assert '自动' in source.text()


# --------------------------------------------------------------------------- #
#                                reload / cancel                               #
# --------------------------------------------------------------------------- #


def test_reload(env):
    source, _ = run(env, f'{COMMAND_PREFIX} reload')
    assert env.applied == [True]
    assert '配置已生效' in source.text()


def test_cancel(env):
    source, _ = run(env, f'{COMMAND_PREFIX} cancel')
    assert '无需取消' in source.text()

    env.scheduler._tick()
    source, _ = run(env, f'{COMMAND_PREFIX} cancel')
    assert '已取消计划' in source.text()
    assert env.scheduler.next_restart() is None


# --------------------------------------------------------------------------- #
#                                  test 指令                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('target', ['1', '每日重启'])
def test_test_command_by_index_and_name(env, target):
    source, _ = run(env, f'{COMMAND_PREFIX} test {target}')
    assert '测试提醒' in source.text()
    assert wait_until(lambda: len(env.server.said) == 2, timeout=5)
    assert env.server.restart_calls == 0


def test_test_command_unknown_target(env):
    source, _ = run(env, f'{COMMAND_PREFIX} test 9')
    assert '第 9 项' in source.text()
    source, _ = run(env, f'{COMMAND_PREFIX} test 不存在的计划')
    assert '找不到' in source.text()


# --------------------------------------------------------------------------- #
#                              enable / disable                                #
# --------------------------------------------------------------------------- #


def test_disable_and_enable_single_schedule(env):
    assert file_schedules(env)[0]['enabled'] is True

    source, _ = run(env, f'{COMMAND_PREFIX} disable 1')
    assert '已禁用计划 #1 每日重启' in source.text()
    assert '已写入配置文件' in source.text()
    assert file_schedules(env)[0]['enabled'] is False
    # 生效：禁用后不再调度
    assert env.scheduler.list_schedules()[0]['enabled'] is False

    source, _ = run(env, f'{COMMAND_PREFIX} enable 每日重启')
    assert '已启用计划 #1 每日重启' in source.text()
    assert file_schedules(env)[0]['enabled'] is True
    assert env.scheduler.list_schedules()[0]['enabled'] is True


def test_enable_works_on_schedule_disabled_in_config(env):
    """配置文件里就是关着的计划，也要能通过指令打开（旧版本做不到）"""
    raw = json.loads(open(env.server.config_file_path, encoding='utf8').read())
    raw['schedules'][0]['enabled'] = False
    with open(env.server.config_file_path, 'w', encoding='utf8') as file_handler:
        json.dump(raw, file_handler, ensure_ascii=False)
    run(env, f'{COMMAND_PREFIX} reload')
    assert env.scheduler.list_schedules()[0]['enabled'] is False

    source, _ = run(env, f'{COMMAND_PREFIX} enable 1')
    assert '已启用' in source.text()
    assert file_schedules(env)[0]['enabled'] is True
    assert env.scheduler.list_schedules()[0]['enabled'] is True


def test_enable_disable_all(env):
    source, _ = run(env, f'{COMMAND_PREFIX} disable')
    assert '已禁用全部 1 个计划' in source.text()
    assert file_schedules(env)[0]['enabled'] is False

    source, _ = run(env, f'{COMMAND_PREFIX} enable all')
    assert '已启用全部 1 个计划' in source.text()
    assert file_schedules(env)[0]['enabled'] is True


def test_enable_out_of_range(env):
    source, _ = run(env, f'{COMMAND_PREFIX} enable 9')
    assert '没有第 9 项' in source.text()


def test_enable_unknown_name(env):
    source, _ = run(env, f'{COMMAND_PREFIX} enable 不存在的计划')
    assert '找不到' in source.text()


def test_enable_broken_schedule_by_index_gives_hint(env):
    raw = json.loads(open(env.server.config_file_path, encoding='utf8').read())
    raw['schedules'].append({'name': '坏的', 'cron': '99 * * * *', 'enabled': False})
    with open(env.server.config_file_path, 'w', encoding='utf8') as file_handler:
        json.dump(raw, file_handler, ensure_ascii=False)

    source, _ = run(env, f'{COMMAND_PREFIX} enable 2')
    assert '已启用计划 #2 坏的' in source.text()
    assert 'cron 有误' in source.text()
    assert file_schedules(env)[1]['enabled'] is True


# --------------------------------------------------------------------------- #
#                                add / remove                                  #
# --------------------------------------------------------------------------- #


def test_add_schedule(env):
    source, _ = run(env, f'{COMMAND_PREFIX} add 凌晨重启 0 6 * * *')
    text = source.text()
    assert '已创建计划 #2 凌晨重启' in text
    assert '每天 06:00' in text
    assert '配置已生效' in text

    schedules = file_schedules(env)
    assert len(schedules) == 2
    assert schedules[1]['name'] == '凌晨重启'
    assert schedules[1]['cron'] == '0 6 * * *'
    assert schedules[1]['enabled'] is True
    assert schedules[1]['use_default_notifications'] is True

    # 新计划立刻参与调度，序号也能用
    assert [entry['name'] for entry in env.scheduler.list_schedules()] == ['每日重启', '凌晨重启']
    source, _ = run(env, f'{COMMAND_PREFIX} disable 2')
    assert '凌晨重启' in source.text()


def test_add_schedule_with_quoted_name(env):
    source, _ = run(env, f'{COMMAND_PREFIX} add "每周一 凌晨" 30 3 * * 1')
    assert '已创建计划 #2 每周一 凌晨' in source.text()
    assert file_schedules(env)[1]['cron'] == '30 3 * * 1'


def test_add_schedule_bad_cron(env):
    source, _ = run(env, f'{COMMAND_PREFIX} add 坏计划 99 * * * *')
    assert 'cron 表达式有误' in source.text()
    assert len(file_schedules(env)) == 1
    assert env.applied == []          # 没写成功就不应该触发重载


def test_add_schedule_duplicate_name(env):
    source, _ = run(env, f'{COMMAND_PREFIX} add 每日重启 0 6 * * *')
    assert '已经存在' in source.text()
    assert len(file_schedules(env)) == 1


def test_add_schedule_needs_name_and_cron(env):
    with pytest.raises(CommandError):
        run(env, f'{COMMAND_PREFIX} add 只有名字')


def test_remove_schedule(env):
    run(env, f'{COMMAND_PREFIX} add 待删除 0 6 * * *')
    assert len(file_schedules(env)) == 2

    source, _ = run(env, f'{COMMAND_PREFIX} remove 2')
    assert '已删除计划 待删除' in source.text()
    assert len(file_schedules(env)) == 1
    assert [entry['name'] for entry in env.scheduler.list_schedules()] == ['每日重启']


def test_remove_schedule_by_name(env):
    run(env, f'{COMMAND_PREFIX} add 待删除 0 6 * * *')
    source, _ = run(env, f'{COMMAND_PREFIX} remove 待删除')
    assert '已删除计划 待删除' in source.text()
    assert len(file_schedules(env)) == 1


def test_remove_out_of_range_and_all(env):
    source, _ = run(env, f'{COMMAND_PREFIX} remove 9')
    assert '第 9 项' in source.text()
    source, _ = run(env, f'{COMMAND_PREFIX} remove all')
    assert '不支持一次删除全部' in source.text()
    assert len(file_schedules(env)) == 1


# --------------------------------------------------------------------------- #
#                                 权限与错误                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('command', [
    f'{COMMAND_PREFIX} list',
    f'{COMMAND_PREFIX} next',
])
def test_public_view_commands_are_open_to_all_players(env, command):
    source, _ = run(env, command, permission=PermissionLevel.USER)
    assert source.replies


@pytest.mark.parametrize('command', [
    f'{COMMAND_PREFIX} status',
    f'{COMMAND_PREFIX} history',
])
def test_view_commands_need_helper(env, command):
    with pytest.raises(RequirementNotMet):
        run(env, command, permission=PermissionLevel.USER)


@pytest.mark.parametrize('command', [
    f'{COMMAND_PREFIX} reload',
    f'{COMMAND_PREFIX} cancel',
    f'{COMMAND_PREFIX} test 1',
    f'{COMMAND_PREFIX} enable 1',
    f'{COMMAND_PREFIX} disable 1',
    f'{COMMAND_PREFIX} add 新计划 0 6 * * *',
    f'{COMMAND_PREFIX} remove 1',
])
def test_admin_commands_need_admin(env, command):
    with pytest.raises(RequirementNotMet):
        run(env, command, permission=PermissionLevel.HELPER)

    # owner / 控制台有最高权限，应当可以执行
    source, _ = run(env, command, permission=PermissionLevel.OWNER)
    assert source.replies


def test_unknown_subcommand(env):
    with pytest.raises(CommandError):
        run(env, f'{COMMAND_PREFIX} 不存在的子命令')


def test_history_with_bad_argument(env):
    with pytest.raises(CommandError):
        run(env, f'{COMMAND_PREFIX} history abc')


def test_suggestions_include_index(env):
    source = FakeCommandSource(PermissionLevel.OWNER, server=env.server)
    suggestions = env.root._entry_generate_suggestions(source, f'{COMMAND_PREFIX} test ')  # noqa: SLF001
    texts = {item.command for item in suggestions} | {item.suggest_input for item in suggestions}
    assert any('1' == text or '每日重启' in text for text in texts)


# --------------------------------------------------------------------------- #
#                              自定义简化指令别名                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture
def env_with_alias():
    return _make_env(alias='!!sr')


def test_alias_command_and_help_are_registered(env_with_alias):
    env = env_with_alias
    assert len(env.server.registered_commands) == 2
    prefixes = {next(iter(node.literals)) for node in env.server.registered_commands}
    assert prefixes == {COMMAND_PREFIX, '!!sr'}
    help_prefixes = {item[0] for item in env.server.help_messages}
    assert help_prefixes == {COMMAND_PREFIX, '!!sr'}


def test_alias_works_like_primary(env_with_alias):
    env = env_with_alias
    alias_root = env.server.registered_commands[1]
    source = FakeCommandSource(PermissionLevel.USER, server=env.server)
    run_command(alias_root, source, '!!sr list')
    assert '[#1] 每日重启' in source.text()


def test_alias_help_mentions_alias(env_with_alias):
    env = env_with_alias
    alias_root = env.server.registered_commands[1]
    source = FakeCommandSource(PermissionLevel.USER, server=env.server)
    run_command(alias_root, source, '!!sr')
    assert '简化指令：!!sr 与 !!srestart 等价' in source.text()


# --------------------------------------------------------------------------- #
#                                权限失败提示                                   #
# --------------------------------------------------------------------------- #


def test_admin_failure_message_mentions_mcdr_permission(env):
    source = FakeCommandSource(PermissionLevel.HELPER, server=env.server, console=False, player='Steve')
    with pytest.raises(RequirementNotMet) as exc_info:
        run_command(env.root, source, f'{COMMAND_PREFIX} reload')
    reason = exc_info.value.get_reason()
    text = reason.to_plain_text() if hasattr(reason, 'to_plain_text') else str(reason)
    assert '需要 MCDR admin 权限' in text
    assert 'Minecraft 的 /op 不等于 MCDR admin' in text
    assert '!!MCDR permission set Steve admin' in text


def test_admin_failure_message_for_console_has_fallback(env):
    source = FakeCommandSource(PermissionLevel.HELPER, server=env.server, console=False, player='')
    with pytest.raises(RequirementNotMet) as exc_info:
        run_command(env.root, source, f'{COMMAND_PREFIX} reload')
    reason = exc_info.value.get_reason()
    text = reason.to_plain_text() if hasattr(reason, 'to_plain_text') else str(reason)
    assert '需要 MCDR admin 权限' in text
