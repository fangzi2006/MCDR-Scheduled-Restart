"""配置文件读改写（enable / disable / add / remove）的测试"""

from __future__ import annotations

import copy
import json

import pytest

from scheduled_restart.config import DEFAULT_CONFIG, Config, default_schedule_entry
from scheduled_restart.config_store import ConfigFileEditor

from fakes import FakeServer


def three_schedules() -> dict:
    """编辑器测试要用多条计划来验证「只改指定那一条」，这里自己造一份"""
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw['schedules'] = [
        default_schedule_entry(name='计划一', enabled=False, cron='0 4 * * *'),
        default_schedule_entry(name='计划二', enabled=False, cron='0 5 * * *'),
        default_schedule_entry(name='计划三', enabled=False, cron='0 6 * * *'),
    ]
    return raw


def make_editor(server: FakeServer, raw: dict = None) -> ConfigFileEditor:
    server.seed_config_file(raw)
    return ConfigFileEditor(server)


def read_file(server: FakeServer) -> dict:
    with open(server.config_file_path, encoding='utf8') as file_handler:
        return json.load(file_handler)


# --------------------------------------------------------------------------- #
#                                    读                                        #
# --------------------------------------------------------------------------- #


def test_read_raw_without_file_returns_defaults():
    server = FakeServer()
    editor = ConfigFileEditor(server)
    raw = editor.read_raw()
    assert raw['schedules'] == DEFAULT_CONFIG['schedules']
    assert not __import__('os').path.isfile(server.config_file_path)   # 只读不写


def test_read_raw_reads_existing_file():
    server = FakeServer()
    editor = make_editor(server)
    raw = editor.read_raw()
    assert len(raw['schedules']) == len(DEFAULT_CONFIG['schedules'])


# --------------------------------------------------------------------------- #
#                                  开关计划                                     #
# --------------------------------------------------------------------------- #


def test_set_enabled_writes_file():
    server = FakeServer()
    editor = make_editor(server, three_schedules())
    assert read_file(server)['schedules'][0]['enabled'] is False    # 造出来的计划都是关闭的

    ok, message = editor.set_enabled(0, True)
    assert ok is True
    assert '已启用' in message and '#1' in message
    assert read_file(server)['schedules'][0]['enabled'] is True
    # 其余计划与顶层字段不受影响
    assert read_file(server)['schedules'][1]['enabled'] is False
    assert read_file(server)['_readme'] == DEFAULT_CONFIG['_readme']


def test_set_enabled_disabled_and_reload_keeps_data():
    server = FakeServer()
    editor = make_editor(server)
    editor.set_enabled(0, True)
    ok, message = editor.set_enabled(0, False)
    assert ok is True
    assert '已禁用' in message
    assert read_file(server)['schedules'][0]['enabled'] is False


def test_set_enabled_out_of_range():
    server = FakeServer()
    editor = make_editor(server)
    ok, message = editor.set_enabled(99, True)
    assert ok is False and '没有第 100 项' in message


def test_set_enabled_on_broken_cron_adds_hint():
    server = FakeServer()
    server.config_raw = copy.deepcopy(DEFAULT_CONFIG)
    server.config_raw['schedules'] = [{'name': '坏的', 'cron': '99 * * * *', 'enabled': False}]
    editor = make_editor(server)

    ok, message = editor.set_enabled(0, True)
    assert ok is True
    assert '已启用' in message
    assert 'cron 有误' in message
    assert read_file(server)['schedules'][0]['enabled'] is True


def test_set_all_enabled():
    server = FakeServer()
    editor = make_editor(server, three_schedules())
    ok, message = editor.set_all_enabled(True)
    assert ok is True and '全部 3 个计划' in message
    assert all(entry['enabled'] is True for entry in read_file(server)['schedules'])

    ok, message = editor.set_all_enabled(False)
    assert ok is True
    assert all(entry['enabled'] is False for entry in read_file(server)['schedules'])


def test_set_all_enabled_without_schedules():
    server = FakeServer()
    server.config_raw = copy.deepcopy(DEFAULT_CONFIG)
    server.config_raw['schedules'] = []
    editor = make_editor(server)
    ok, message = editor.set_all_enabled(True)
    assert ok is False and '没有任何计划' in message


# --------------------------------------------------------------------------- #
#                                  新建计划                                     #
# --------------------------------------------------------------------------- #


def test_add_schedule():
    server = FakeServer()
    server.config_raw = copy.deepcopy(DEFAULT_CONFIG)
    server.config_raw['schedules'] = []
    editor = make_editor(server)

    ok, message = editor.add_schedule('每日重启', '0 4 * * *')
    assert ok is True
    assert '#1' in message and '每天 04:00' in message and '已启用' in message

    schedules = read_file(server)['schedules']
    assert len(schedules) == 1
    entry = schedules[0]
    assert entry['name'] == '每日重启'
    assert entry['cron'] == '0 4 * * *'
    assert entry['enabled'] is True
    assert entry['use_default_notifications'] is True
    assert entry['restart_method'] == 'mcdr_restart'
    # 新建出来的条目必须是插件自己认得的合法计划
    config = Config.from_raw(read_file(server))
    assert config.errors == []
    assert config.schedules[0].name == '每日重启'
    assert config.schedules[0].enabled is True


def test_add_schedule_normalizes_whitespace():
    server = FakeServer()
    server.config_raw = copy.deepcopy(DEFAULT_CONFIG)
    server.config_raw['schedules'] = []
    editor = make_editor(server)
    ok, _ = editor.add_schedule('多空格', '0   4  *  *  *')
    assert ok is True
    assert read_file(server)['schedules'][0]['cron'] == '0 4 * * *'


@pytest.mark.parametrize('cron', ['99 * * * *', '* * * *', 'abc', ''])
def test_add_schedule_rejects_bad_cron(cron):
    server = FakeServer()
    server.config_raw = copy.deepcopy(DEFAULT_CONFIG)
    server.config_raw['schedules'] = []
    editor = make_editor(server)
    ok, message = editor.add_schedule('坏计划', cron)
    assert ok is False
    assert 'cron' in message
    assert read_file(server)['schedules'] == []      # 不会写坏文件


def test_add_schedule_rejects_duplicate_name():
    server = FakeServer()
    editor = make_editor(server)
    existing = read_file(server)['schedules'][0]['name']
    ok, message = editor.add_schedule(existing, '0 4 * * *')
    assert ok is False and '已经存在' in message


def test_add_schedule_rejects_empty_and_numeric_name():
    server = FakeServer()
    editor = make_editor(server)
    assert editor.add_schedule('   ', '0 4 * * *')[0] is False
    ok, message = editor.add_schedule('12', '0 4 * * *')
    assert ok is False and '纯数字' in message


# --------------------------------------------------------------------------- #
#                                  删除计划                                     #
# --------------------------------------------------------------------------- #


def test_remove_schedule():
    server = FakeServer()
    editor = make_editor(server, three_schedules())
    before = read_file(server)['schedules']
    ok, message = editor.remove_schedule(1)
    assert ok is True
    assert before[1]['name'] in message

    after = read_file(server)['schedules']
    assert len(after) == len(before) - 1
    assert after[0]['name'] == before[0]['name']
    assert after[1]['name'] == before[2]['name']
    assert Config.from_raw(read_file(server)).errors == []


def test_remove_schedule_out_of_range():
    server = FakeServer()
    editor = make_editor(server)
    ok, message = editor.remove_schedule(5)
    assert ok is False and '没有第 6 项' in message


def test_remove_schedule_keeps_other_content():
    """用户自己加的字段不能被我们抹掉"""
    server = FakeServer()
    raw = three_schedules()
    raw['我的备注'] = '不要删我'
    raw['schedules'][0]['我自己加的字段'] = 42
    server.config_raw = raw
    editor = make_editor(server, raw)

    editor.set_enabled(1, True)
    saved = read_file(server)
    assert saved['我的备注'] == '不要删我'
    assert saved['schedules'][0]['我自己加的字段'] == 42
    assert saved['schedules'][1]['enabled'] is True


def test_write_is_atomic_leaves_no_temp_file():
    import os
    server = FakeServer()
    editor = make_editor(server)
    editor.set_enabled(0, True)
    assert not os.path.exists(server.config_file_path + '.tmp')


def test_broken_schedules_field():
    server = FakeServer()
    server.seed_config_file({'schedules': '这不是数组'})
    editor = ConfigFileEditor(server)
    with pytest.raises(ValueError):
        editor.set_enabled(0, True)


def test_read_raw_tolerates_bom():
    """带 BOM 的配置文件也要能读，并且写回时不再带 BOM"""
    import codecs

    server = FakeServer()
    server.seed_config_file()
    with open(server.config_file_path, 'rb') as file_handler:
        content = file_handler.read()
    with open(server.config_file_path, 'wb') as file_handler:
        file_handler.write(codecs.BOM_UTF8 + content)

    editor = ConfigFileEditor(server)
    ok, message = editor.set_enabled(0, True)
    assert ok is True
    with open(server.config_file_path, 'rb') as file_handler:
        assert not file_handler.read().startswith(codecs.BOM_UTF8)
    assert read_file(server)['schedules'][0]['enabled'] is True
