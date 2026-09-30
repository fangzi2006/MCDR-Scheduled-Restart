"""配置解析与容错行为的测试"""

from __future__ import annotations

import copy
import json

import pytest

from scheduled_restart.config import (
    DEFAULT_CONFIG,
    Config,
    load_config,
)

from fakes import FakeServer


def base_config(**overrides) -> dict:
    config = {
        'enabled': True,
        'default_notifications': copy.deepcopy(DEFAULT_CONFIG['default_notifications']),
        'schedules': [
            {
                'name': '每日重启',
                'enabled': True,
                'cron': '0 4 * * *',
            },
        ],
    }
    config.update(overrides)
    return config


# --------------------------------------------------------------------------- #
#                                  默认配置                                     #
# --------------------------------------------------------------------------- #


def test_default_config_is_valid_and_silent():
    """自带默认配置必须零错误零警告，否则用户开箱就会看到报错"""
    config = Config.from_raw(copy.deepcopy(DEFAULT_CONFIG))
    assert config.errors == []
    assert config.warnings == []
    assert config.needs_save is False
    assert len(config.schedules) == 1
    assert all(not schedule.enabled for schedule in config.schedules)
    assert len(config.default_notifications) == 1
    # 默认配置里不应出现需要 JSON 转义的字符（说明文字里不要写带引号的示例）
    dumped = json.dumps(config.to_raw(), ensure_ascii=False)
    assert '\\"' not in dumped
    assert '\\\\' not in dumped


def test_default_config_round_trip():
    config = Config.from_raw(copy.deepcopy(DEFAULT_CONFIG))
    again = Config.from_raw(config.to_raw())
    assert again.warnings == [] and again.errors == []
    assert [item.name for item in again.schedules] == [item.name for item in config.schedules]
    assert [item.advance_seconds for item in again.default_notifications] == \
           [item.advance_seconds for item in config.default_notifications]


# --------------------------------------------------------------------------- #
#                                正常配置解析                                   #
# --------------------------------------------------------------------------- #


def test_parse_arrays_of_objects():
    raw = base_config(schedules=[
        {
            'name': '每日',
            'cron': '0 4 * * *',
            'restart_method': 'mcdr_restart',
            'notifications': [
                {'advance': '5m', 'type': 'chat', 'message': '还有 5 分钟'},
                {'advance_seconds': 10, 'type': 'title', 'title': '马上重启', 'subtitle': '剩余 {remaining}',
                 'times': {'fade_in': 0.5, 'stay': 2, 'fade_out': 0.5}},
            ],
        },
        {
            'name': '每周',
            'cron': '30 3 * * 1',
            'use_default_notifications': True,
        },
    ])
    config = Config.from_raw(raw)
    assert config.errors == [] and config.warnings == []
    assert len(config.schedules) == 2

    daily = config.schedules[0]
    assert daily.cron_expression == '0 4 * * *'
    assert daily.use_default_notifications is False
    assert [item.advance_seconds for item in daily.notifications] == [300.0, 10.0]
    assert daily.notifications[0].type == 'chat'
    assert daily.notifications[1].title == '马上重启'
    assert daily.notifications[1].stay == 2.0

    weekly = config.schedules[1]
    assert weekly.use_default_notifications is True
    # 使用全局默认提醒
    assert len(config.notifications_for(weekly)) == len(config.default_notifications)


def test_notifications_key_implies_own_notifications():
    config = Config.from_raw(base_config(schedules=[{
        'name': 'x',
        'cron': '@daily',
        'notifications': [{'type': 'chat', 'message': 'hi'}],
    }]))
    assert config.schedules[0].use_default_notifications is False


def test_restart_method_aliases():
    config = Config.from_raw(base_config(schedules=[{'name': 'x', 'cron': '@daily', 'restart_method': 'restart'}]))
    assert config.schedules[0].restart_method == 'mcdr_restart'


def test_notification_defaults():
    config = Config.from_raw(base_config(schedules=[{
        'name': 'x',
        'cron': '@daily',
        'notifications': [{'type': 'chat', 'message': 'hi'}],
    }]))
    notification = config.schedules[0].notifications[0]
    assert notification.enabled is True
    assert notification.advance_seconds == 60.0
    assert notification.fade_in == 1.0 and notification.stay == 4.0 and notification.fade_out == 1.0
    assert notification.color is None and notification.sound is None


# --------------------------------------------------------------------------- #
#                                  容错行为                                     #
# --------------------------------------------------------------------------- #


def test_bad_cron_skips_only_that_schedule():
    config = Config.from_raw(base_config(schedules=[
        {'name': '坏的', 'cron': '99 * * * *'},
        {'name': '好的', 'cron': '0 4 * * *'},
    ]))
    assert len(config.schedules) == 1
    assert config.schedules[0].name == '好的'
    assert len(config.errors) == 1
    assert '坏' in config.errors[0] or 'cron' in config.errors[0]
    # 有错误时不能回写配置，否则用户写错的计划会被抹掉
    assert config.needs_save is False


def test_missing_cron_is_an_error():
    config = Config.from_raw(base_config(schedules=[{'name': 'x'}]))
    assert config.schedules == []
    assert len(config.errors) == 1


def test_wrong_types_fall_back_with_warning():
    config = Config.from_raw(base_config(
        enabled='yes',
        check_interval_seconds='1s',
        notify_on_join=1,
        schedules=[{
            'name': 123,
            'cron': '0 4 * * *',
            'enabled': 'true',
            'restart_delay_seconds': '30s',
            'notifications': [
                {'advance_seconds': 'abc', 'type': 'chat', 'message': 'hi'},
                {'advance_seconds': 60, 'type': '不存在的类型', 'message': 'hi'},
                {'advance_seconds': 60, 'type': 'title'},
                {'advance_seconds': 60, 'type': 'chat', 'message': ''},
                12345,
            ],
        }],
    ))
    assert config.enabled is True                       # 回退默认
    assert config.notify_on_join is True
    assert config.check_interval_seconds == 1.0
    assert config.errors == []
    assert len(config.warnings) > 5
    assert config.needs_save is True                    # 有警告时回写规范化配置

    schedule = config.schedules[0]
    assert schedule.name == '计划1'                      # 名字类型错误 -> 默认名
    assert schedule.enabled is True
    assert schedule.restart_delay_seconds == 30.0        # "30s" 被正确解析

    notifications = schedule.notifications
    assert [item.advance_seconds for item in notifications] == [60.0, 60.0, 60.0]
    assert notifications[0].type == 'chat'
    assert notifications[1].type == 'chat'               # 未知类型回退 chat
    title = [item for item in notifications if item.title][0]
    assert title.subtitle == '剩余 {remaining}'          # title 缺内容时补默认文案


def test_duplicate_names_are_renamed():
    config = Config.from_raw(base_config(schedules=[
        {'name': '同名', 'cron': '@daily'},
        {'name': '同名', 'cron': '@hourly'},
    ]))
    assert [item.name for item in config.schedules] == ['同名', '同名#2']
    assert any('重复' in warning for warning in config.warnings)


def test_invalid_timezone_falls_back():
    config = Config.from_raw(base_config(timezone='Mars/Olympus'))
    assert config.timezone is None
    assert any('时区' in warning for warning in config.warnings)


def test_empty_schedules_warns():
    config = Config.from_raw(base_config(schedules=[]))
    assert config.schedules == []
    assert any('没有任何有效的重启计划' in warning for warning in config.warnings)


def test_advance_accepts_duration_strings():
    config = Config.from_raw(base_config(default_notifications=[
        {'advance': '1h', 'type': 'chat', 'message': 'a'},
        {'advance_seconds': 90, 'type': 'chat', 'message': 'b'},
    ]))
    assert [item.advance_seconds for item in config.default_notifications] == [3600.0, 90.0]


# --------------------------------------------------------------------------- #
#                                 load_config                                  #
# --------------------------------------------------------------------------- #


def test_load_config_uses_mcdr_and_saves_when_warnings():
    server = FakeServer()
    server.config_raw = base_config(schedules=[{'name': 123, 'cron': '0 4 * * *'}])
    config = load_config(server)
    assert server.loaded_config_file_name == 'config.json'
    assert server.saved_config is not None
    assert server.saved_config['schedules'][0]['name'] == '计划1'


def test_load_config_does_not_save_on_errors():
    server = FakeServer()
    server.config_raw = base_config(schedules=[{'name': 'x', 'cron': '坏掉了'}])
    config = load_config(server)
    assert config.errors
    assert server.saved_config is None


# --------------------------------------------------------------------------- #
#                              配置文件读取前处理                                #
# --------------------------------------------------------------------------- #


def test_bom_is_stripped_so_plans_are_not_lost():
    """带 UTF-8 BOM 的配置文件（记事本/PowerShell 常见）不能导致配置被重置"""
    import codecs
    import json as json_module

    server = FakeServer()
    raw = base_config(schedules=[{'name': '我的计划', 'cron': '0 4 * * *'}])
    with open(server.config_file_path, 'wb') as file_handler:
        file_handler.write(codecs.BOM_UTF8 + json_module.dumps(raw, ensure_ascii=False).encode('utf8'))

    config = load_config(server)
    assert [schedule.name for schedule in config.schedules] == ['我的计划']
    assert config.errors == []

    with open(server.config_file_path, 'rb') as file_handler:
        content = file_handler.read()
    assert not content.startswith(codecs.BOM_UTF8)                 # BOM 已被去掉
    assert json_module.loads(content.decode('utf8'))['schedules'][0]['name'] == '我的计划'


def test_broken_config_is_backed_up_before_regeneration():
    """配置文件彻底坏掉时先备份，避免 MCDR 用默认配置覆盖后找不回来"""
    import os

    server = FakeServer()
    with open(server.config_file_path, 'w', encoding='utf8') as file_handler:
        file_handler.write('{ 这不是 JSON')

    load_config(server)          # 不抛异常：MCDR 会按 regen 策略用默认值

    backups = [
        name for name in os.listdir(server.data_folder) if name.startswith('config.json.broken-')
    ]
    assert len(backups) == 1
    with open(os.path.join(server.data_folder, backups[0]), encoding='utf8') as file_handler:
        assert file_handler.read() == '{ 这不是 JSON'


def test_preprocess_does_nothing_for_valid_config():
    server = FakeServer()
    server.seed_config_file()
    with open(server.config_file_path, encoding='utf8') as file_handler:
        before = file_handler.read()

    config = load_config(server)
    assert config.errors == []
    with open(server.config_file_path, encoding='utf8') as file_handler:
        assert file_handler.read() == before
    assert not [name for name in __import__('os').listdir(server.data_folder) if '.broken-' in name]
