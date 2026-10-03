"""配置的读取、校验与规范化。

配置以「数组里放对象」的形式描述计划与提醒，例如::

    {
      "schedules": [
        {
          "name": "每日凌晨重启",
          "cron": "0 4 * * *",
          "notifications": [
            {"advance_time": 300, "type": "chat",  "message": "§e5 分钟后重启"},
            {"advance_time": 60,  "type": "title", "title": "重启倒计时", "subtitle": "剩余 {remaining}"}
          ]
        }
      ]
    }

本模块尽量「容错」：单个字段写错只影响该字段（回退默认值并给出警告），
单个计划写错只跳过该计划（给出错误），插件本身不会被错误的配置搞崩。
"""

from __future__ import annotations

import codecs
import copy
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .cron import CronError, CronSchedule, parse_cron, parse_duration

__all__ = [
    'DEFAULT_CONFIG',
    'Notification',
    'Schedule',
    'Config',
    'load_config',
    'default_schedule_entry',
    'config_file_path',
    'preprocess_config_file',
    'RESTART_METHODS',
]

#: 支持的重启方式
RESTART_METHODS = ('mcdr_restart', 'stop', 'stop_exit', 'custom', 'none')

_RESTART_METHOD_ALIASES = {
    'restart': 'mcdr_restart',
    'mcdr': 'mcdr_restart',
    'mcdr-restart': 'mcdr_restart',
    'soft_stop': 'stop',
    'stop_server': 'stop',
    'exit': 'stop_exit',
    'stop_and_exit': 'stop_exit',
    'command': 'custom',
    'skip': 'none',
    'nothing': 'none',
}

NOTIFICATION_TYPES = ('chat', 'title', 'actionbar', 'command')

#: ``sound_position`` 的特殊值：在每个玩家自己的位置播放音效（1.13+，用 execute as/at 实现）
PER_PLAYER_POSITION = '@s'

_CANONICAL_METHOD = {
    'mcdr_restart': 'mcdr_restart',
    'stop': 'stop',
    'stop_exit': 'stop_exit',
    'custom': 'custom',
    'none': 'none',
}

# --------------------------------------------------------------------------- #
#                                   读值工具                                   #
# --------------------------------------------------------------------------- #


class _Issues:
    """收集配置问题，供日志与 ``!!srestart status`` 展示"""

    def __init__(self) -> None:
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def warn(self, path: str, message: str) -> None:
        self.warnings.append(f'{path}: {message}')

    def error(self, path: str, message: str) -> None:
        self.errors.append(f'{path}: {message}')

    def merge(self, other: '_Issues', prefix: str = '') -> None:
        self.warnings.extend(f'{prefix}{item}' for item in other.warnings)
        self.errors.extend(f'{prefix}{item}' for item in other.errors)


def _read_bool(raw: Dict[str, Any], key: str, default: bool, path: str, issues: _Issues) -> bool:
    if key not in raw or raw[key] is None:
        return default
    value = raw[key]
    if isinstance(value, bool):
        return value
    issues.warn(f'{path}.{key}', f'需要 true/false，实际为 {value!r}，已回退默认值 {default}')
    return default


def _read_number(
        raw: Dict[str, Any], key: str, default: float, path: str, issues: _Issues,
        minimum: Optional[float] = None, maximum: Optional[float] = None,
) -> float:
    if key not in raw or raw[key] is None:
        return default
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        issues.warn(f'{path}.{key}', f'需要数字，实际为 {value!r}，已回退默认值 {default}')
        return default
    try:
        number = parse_duration(value, field_name=f'{path}.{key}')
    except CronError as e:
        issues.warn(f'{path}.{key}', f'{e}，已回退默认值 {default}')
        return default
    if minimum is not None and number < minimum:
        issues.warn(f'{path}.{key}', f'不能小于 {minimum}，实际为 {number}，已回退默认值 {default}')
        return default
    if maximum is not None and number > maximum:
        issues.warn(f'{path}.{key}', f'不能大于 {maximum}，实际为 {number}，已回退默认值 {default}')
        return default
    return float(number)


def _read_int(
        raw: Dict[str, Any], key: str, default: int, path: str, issues: _Issues,
        minimum: Optional[int] = None, maximum: Optional[int] = None,
) -> int:
    value = _read_number(raw, key, default, path, issues, minimum, maximum)
    return int(round(value))


def _read_str(raw: Dict[str, Any], key: str, default: str, path: str, issues: _Issues) -> str:
    if key not in raw or raw[key] is None:
        return default
    value = raw[key]
    if isinstance(value, str):
        return value
    issues.warn(f'{path}.{key}', f'需要字符串，实际为 {value!r}，已回退默认值 {default!r}')
    return default


def _read_optional_str(raw: Dict[str, Any], key: str, default: Optional[str], path: str, issues: _Issues) -> Optional[str]:
    if key not in raw:
        return default
    value = raw[key]
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() != '' else None
    issues.warn(f'{path}.{key}', f'需要字符串或 null，实际为 {value!r}，已回退默认值 {default!r}')
    return default


def _read_list(raw: Dict[str, Any], key: str, default: List[Any], path: str, issues: _Issues) -> List[Any]:
    if key not in raw or raw[key] is None:
        return list(default)
    value = raw[key]
    if isinstance(value, list):
        return value
    issues.warn(f'{path}.{key}', f'需要数组，实际为 {value!r}，已回退默认值')
    return list(default)


def _read_dict(raw: Dict[str, Any], key: str, default: Dict[str, Any], path: str, issues: _Issues) -> Dict[str, Any]:
    if key not in raw or raw[key] is None:
        return dict(default)
    value = raw[key]
    if isinstance(value, dict):
        return value
    issues.warn(f'{path}.{key}', f'需要对象，实际为 {value!r}，已回退默认值')
    return dict(default)


# --------------------------------------------------------------------------- #
#                                  提醒 (Notification)                         #
# --------------------------------------------------------------------------- #


@dataclass
class Notification:
    """一条提醒

    :param advance_time: 提前多久发送（相对真正重启的时刻）。可以是纯数字秒数，
        也可以是 ``5m`` / ``1h30m`` / ``1天2小时`` 这类时长文本。
    :param type: 通知方式，``chat``(聊天框) / ``title``(大标题) / ``actionbar`` / ``command``
    """

    enabled: bool = True
    advance_time: float = 60.0
    type: str = 'chat'
    message: str = ''
    title: str = ''
    subtitle: str = ''
    fade_in: float = 1.0
    stay: float = 4.0
    fade_out: float = 1.0
    color: Optional[str] = None
    sound: Optional[str] = None
    sound_source: str = 'master'
    #: 音效播放位置："@s"（默认，每个玩家在自己位置听到，需要 1.13+）、
    #: 坐标文本（如 "~ ~ ~" / "100 64 100"）或空字符串（省略坐标与音量音调）
    sound_position: str = PER_PLAYER_POSITION
    sound_volume: float = 1.0
    sound_pitch: float = 1.0
    command: str = ''

    @property
    def is_valid(self) -> bool:
        if self.type == 'command':
            return self.command.strip() != ''
        if self.type == 'title':
            return self.title.strip() != '' or self.subtitle.strip() != ''
        return self.message.strip() != ''

    def to_raw(self) -> Dict[str, Any]:
        return {
            'enabled': self.enabled,
            'advance_time': round(self.advance_time, 3),
            'type': self.type,
            'message': self.message,
            'title': self.title,
            'subtitle': self.subtitle,
            'times': {
                'fade_in': round(self.fade_in, 3),
                'stay': round(self.stay, 3),
                'fade_out': round(self.fade_out, 3),
            },
            'color': self.color,
            'sound': self.sound,
            'sound_source': self.sound_source,
            'sound_position': self.sound_position,
            'sound_volume': round(self.sound_volume, 3),
            'sound_pitch': round(self.sound_pitch, 3),
            'command': self.command,
        }


def _parse_notification(raw: Any, path: str, issues: _Issues) -> Optional[Notification]:
    if not isinstance(raw, dict):
        # 只跳过这一条提醒，不影响整个计划
        issues.warn(path, f'提醒必须是对象（{{...}}），实际为 {raw!r}，已跳过该条提醒')
        return None

    notification = Notification()
    notification.enabled = _read_bool(raw, 'enabled', True, path, issues)

    advance_value = raw.get('advance_time')
    if advance_value is None:
        notification.advance_time = 60.0
    else:
        try:
            notification.advance_time = parse_duration(advance_value, field_name=f'{path}.advance_time')
        except CronError as e:
            issues.warn(f'{path}.advance_time', f'{e}，已回退默认值 60')
            notification.advance_time = 60.0

    notification.type = _read_str(raw, 'type', 'chat', path, issues).strip().lower()
    if notification.type not in NOTIFICATION_TYPES:
        issues.warn(
            f'{path}.type',
            f'未知的通知方式 {notification.type!r}，可用值：{"、".join(NOTIFICATION_TYPES)}，已回退 chat'
        )
        notification.type = 'chat'

    notification.message = _read_str(raw, 'message', '', path, issues)
    notification.title = _read_str(raw, 'title', '', path, issues)
    notification.subtitle = _read_str(raw, 'subtitle', '', path, issues)
    notification.color = _read_optional_str(raw, 'color', None, path, issues)
    notification.sound = _read_optional_str(raw, 'sound', None, path, issues)
    notification.sound_source = _read_str(raw, 'sound_source', 'master', path, issues)
    if 'sound_position' not in raw:
        notification.sound_position = PER_PLAYER_POSITION
    elif raw['sound_position'] is None:
        notification.sound_position = ''
    elif isinstance(raw['sound_position'], str):
        notification.sound_position = raw['sound_position'].strip()
    else:
        issues.warn(
            f'{path}.sound_position',
            f'需要字符串（"@s"、坐标文本或 ""），实际为 {raw["sound_position"]!r}，已回退默认值 {PER_PLAYER_POSITION!r}'
        )
        notification.sound_position = PER_PLAYER_POSITION
    notification.sound_volume = _read_number(raw, 'sound_volume', 1.0, path, issues, minimum=0.0, maximum=1000.0)
    notification.sound_pitch = _read_number(raw, 'sound_pitch', 1.0, path, issues, minimum=0.0, maximum=1000.0)
    notification.command = _read_str(raw, 'command', '', path, issues)

    times = _read_dict(raw, 'times', {}, path, issues)
    if times:
        notification.fade_in = _read_number(times, 'fade_in', 1.0, f'{path}.times', issues, minimum=0.0, maximum=3600.0)
        notification.stay = _read_number(times, 'stay', 4.0, f'{path}.times', issues, minimum=0.0, maximum=3600.0)
        notification.fade_out = _read_number(times, 'fade_out', 1.0, f'{path}.times', issues, minimum=0.0, maximum=3600.0)
    else:
        notification.fade_in = _read_number(raw, 'fade_in', 1.0, path, issues, minimum=0.0, maximum=3600.0)
        notification.stay = _read_number(raw, 'stay', 4.0, path, issues, minimum=0.0, maximum=3600.0)
        notification.fade_out = _read_number(raw, 'fade_out', 1.0, path, issues, minimum=0.0, maximum=3600.0)

    if notification.type == 'title' and notification.title.strip() == '' and notification.subtitle.strip() == '':
        notification.title = '服务器重启'
        notification.subtitle = '剩余 {remaining}'
        issues.warn(path, 'title 类型提醒缺少 title/subtitle，已填入默认文案')

    if not notification.is_valid:
        issues.warn(path, f'{notification.type} 类型提醒内容为空，已跳过该条提醒')
        return None
    return notification


def _parse_notification_list(raw_list: List[Any], path: str, issues: _Issues) -> List[Notification]:
    result: List[Notification] = []
    for index, item in enumerate(raw_list):
        notification = _parse_notification(item, f'{path}[{index}]', issues)
        if notification is not None:
            result.append(notification)
    return result


# --------------------------------------------------------------------------- #
#                                  计划 (Schedule)                             #
# --------------------------------------------------------------------------- #


@dataclass
class Schedule:
    """一条重启计划"""

    name: str
    cron: CronSchedule
    enabled: bool = True
    restart_method: str = 'mcdr_restart'
    custom_command: str = 'stop'
    custom_auto_start: bool = False
    restart_delay_seconds: float = 0.0
    kick_players: bool = False
    kick_message: str = '服务器正在重启，请稍后重新连接'
    use_default_notifications: bool = True
    notifications: List[Notification] = field(default_factory=list)
    #: 该计划在配置文件 ``schedules`` 数组里的下标（0 起）。
    #: 指令里显示的序号是它 +1；即使用户写错了某条计划被跳过，序号也不会错位。
    source_index: int = 0

    @property
    def display_index(self) -> int:
        """给用户看的 1 起序号"""
        return self.source_index + 1

    @property
    def cron_expression(self) -> str:
        return self.cron.expression

    def to_raw(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'enabled': self.enabled,
            'cron': self.cron.expression,
            'restart_method': self.restart_method,
            'custom_command': self.custom_command,
            'custom_auto_start': self.custom_auto_start,
            'restart_delay_seconds': round(self.restart_delay_seconds, 3),
            'kick_players': self.kick_players,
            'kick_message': self.kick_message,
            'use_default_notifications': self.use_default_notifications,
            'notifications': [item.to_raw() for item in self.notifications],
        }


def _parse_schedule(raw: Any, index: int, issues: _Issues, used_names: set) -> Optional[Schedule]:
    path = f'schedules[{index}]'
    if not isinstance(raw, dict):
        issues.error(path, f'计划必须是对象（{{...}}），实际为 {raw!r}，已跳过')
        return None

    local = _Issues()
    name = _read_str(raw, 'name', '', path, local).strip()
    if name == '':
        name = f'计划{index + 1}'
        local.warn(path, f'缺少 name，已使用 {name!r}')

    cron_text = _read_str(raw, 'cron', '', path, local).strip()
    cron: Optional[CronSchedule] = None
    if cron_text == '':
        local.error(path, '缺少 cron 表达式（如 "0 4 * * *"），已跳过该计划')
    else:
        try:
            cron = parse_cron(cron_text)
        except CronError as e:
            local.error(path, f'cron 表达式有误：{e}；已跳过该计划')

    if local.errors:
        issues.merge(local, prefix='')
        return None

    assert cron is not None
    method = _read_str(raw, 'restart_method', 'mcdr_restart', path, local).strip().lower()
    method = _RESTART_METHOD_ALIASES.get(method, method)
    if method not in RESTART_METHODS:
        local.warn(
            f'{path}.restart_method',
            f'未知的重启方式 {method!r}，可用值：{"、".join(RESTART_METHODS)}，已回退 mcdr_restart'
        )
        method = 'mcdr_restart'
    method = _CANONICAL_METHOD[method]

    if name in used_names:
        suffix = 2
        while f'{name}#{suffix}' in used_names:
            suffix += 1
        local.warn(path, f'计划名 {name!r} 重复，已重命名为 {name}#{suffix}')
        name = f'{name}#{suffix}'
    used_names.add(name)

    notifications_raw = raw.get('notifications')
    has_own_notifications = isinstance(notifications_raw, list) and len(notifications_raw) > 0
    if 'use_default_notifications' in raw:
        use_default = _read_bool(raw, 'use_default_notifications', True, path, local)
    else:
        use_default = not has_own_notifications

    notifications: List[Notification] = []
    if not use_default:
        if not has_own_notifications:
            local.warn(path, 'use_default_notifications 为 false 但没有配置 notifications，该计划不会发送任何提醒')
        else:
            notifications = _parse_notification_list(notifications_raw, f'{path}.notifications', local)

    schedule = Schedule(
        name=name,
        cron=cron,
        enabled=_read_bool(raw, 'enabled', True, path, local),
        restart_method=method,
        custom_command=_read_str(raw, 'custom_command', 'stop', path, local),
        custom_auto_start=_read_bool(raw, 'custom_auto_start', False, path, local),
        restart_delay_seconds=_read_number(raw, 'restart_delay_seconds', 0.0, path, local, minimum=0.0, maximum=86400.0),
        kick_players=_read_bool(raw, 'kick_players', False, path, local),
        kick_message=_read_str(raw, 'kick_message', '服务器正在重启，请稍后重新连接', path, local),
        use_default_notifications=use_default,
        notifications=notifications,
        source_index=index,
    )
    issues.merge(local, prefix='')
    return schedule


# --------------------------------------------------------------------------- #
#                                   根配置                                     #
# --------------------------------------------------------------------------- #


@dataclass
class Config:
    enabled: bool = True
    readme: List[str] = field(default_factory=list)
    timezone: Optional[str] = None
    check_interval_seconds: float = 1.0
    skip_missed_notifications: bool = True
    notify_on_join: bool = True
    join_message: str = '§e[定时重启] §f服务器将在 §b{remaining} §f后重启（§b{time}§f）'
    log_history: bool = True
    history_size: int = 100
    command_alias: Optional[str] = None
    default_notifications: List[Notification] = field(default_factory=list)
    schedules: List[Schedule] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    needs_save: bool = False

    # ------------------------------------------------------------------ 方法

    def notifications_for(self, schedule: Schedule) -> List[Notification]:
        """返回某个计划真正生效的提醒列表"""
        if schedule.use_default_notifications:
            return list(self.default_notifications)
        return list(schedule.notifications)

    def get_schedule(self, name: str) -> Optional[Schedule]:
        for schedule in self.schedules:
            if schedule.name == name:
                return schedule
        return None

    def to_raw(self) -> Dict[str, Any]:
        return {
            'enabled': self.enabled,
            '_readme': self.readme,
            'timezone': self.timezone,
            'check_interval_seconds': round(self.check_interval_seconds, 3),
            'skip_missed_notifications': self.skip_missed_notifications,
            'notify_on_join': self.notify_on_join,
            'join_message': self.join_message,
            'log_history': self.log_history,
            'history_size': self.history_size,
            'command_alias': self.command_alias or '',
            'default_notifications': [item.to_raw() for item in self.default_notifications],
            'schedules': [item.to_raw() for item in self.schedules],
        }

    # ---------------------------------------------------------------- 解析

    @classmethod
    def from_raw(cls, raw: Any) -> 'Config':
        issues = _Issues()
        if not isinstance(raw, dict):
            issues.error('config', f'配置文件根节点必须是对象，实际为 {type(raw).__name__}，已全部使用默认值')
            raw = {}

        config = cls()
        config.enabled = _read_bool(raw, 'enabled', True, 'config', issues)
        config.readme = [str(item) for item in _read_list(raw, '_readme', DEFAULT_README, 'config', issues)]

        timezone_name = _read_optional_str(raw, 'timezone', None, 'config', issues)
        if timezone_name is not None:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(timezone_name)
            except Exception as e:
                issues.warn(
                    'config.timezone',
                    f'无法加载时区 {timezone_name!r}（{e}），已回退为跟随 MCDR 进程的本地时区'
                )
                timezone_name = None
        config.timezone = timezone_name

        config.check_interval_seconds = _read_number(
            raw, 'check_interval_seconds', 1.0, 'config', issues, minimum=0.2, maximum=60.0
        )
        config.skip_missed_notifications = _read_bool(raw, 'skip_missed_notifications', True, 'config', issues)
        config.notify_on_join = _read_bool(raw, 'notify_on_join', True, 'config', issues)
        config.join_message = _read_str(raw, 'join_message', cls.join_message, 'config', issues)
        config.log_history = _read_bool(raw, 'log_history', True, 'config', issues)
        config.history_size = _read_int(raw, 'history_size', 100, 'config', issues, minimum=1, maximum=100000)

        alias = _read_optional_str(raw, 'command_alias', None, 'config', issues)
        if alias is not None:
            alias = alias.strip()
            if alias == '':
                alias = None
            elif not alias.startswith('!!'):
                issues.warn(
                    'config.command_alias',
                    f'自定义指令别名必须以 !! 开头，实际为 {alias!r}，已忽略'
                )
                alias = None
            elif alias == '!!srestart':
                issues.warn(
                    'config.command_alias',
                    '自定义指令别名不能与默认指令 !!srestart 重复，已忽略'
                )
                alias = None
            elif ' ' in alias:
                issues.warn(
                    'config.command_alias',
                    f'自定义指令别名不能包含空格，实际为 {alias!r}，已忽略'
                )
                alias = None
        config.command_alias = alias

        config.default_notifications = _parse_notification_list(
            _read_list(raw, 'default_notifications', [], 'config', issues), 'default_notifications', issues
        )

        used_names: set = set()
        schedules: List[Schedule] = []
        for index, item in enumerate(_read_list(raw, 'schedules', [], 'config', issues)):
            schedule = _parse_schedule(item, index, issues, used_names)
            if schedule is not None:
                schedules.append(schedule)
        config.schedules = schedules

        if not config.schedules:
            issues.warn('config.schedules', '没有任何有效的重启计划，插件当前不会自动重启服务器')

        config.warnings = issues.warnings
        config.errors = issues.errors
        # 有错误时不要回写文件，避免把用户写错的计划从配置里抹掉
        config.needs_save = bool(issues.warnings) and not issues.errors
        return config


# --------------------------------------------------------------------------- #
#                                  默认配置                                    #
# --------------------------------------------------------------------------- #

DEFAULT_README = [
    '本文件由 MCDR 插件 scheduled_restart 生成，改完用 !!srestart reload 热重载。',
    'cron 使用 Linux cron 格式：5 个字段是「分 时 日 月 周」，6 个字段是「秒 分 时 日 月 周」。',
    '  示例：0 4 * * *      每天 04:00',
    '        30 3 * * 1     每周一 03:30',
    '        0 5 1 * *      每月 1 日 05:00',
    '        0 */6 * * *    每 6 小时',
    '        @daily         每天 00:00（支持 @hourly/@daily/@weekly/@monthly/@yearly 宏）',
    '提醒写在 notifications 数组里，每条是一个对象：',
    '  advance_time 提前多久发送；可以写纯数字秒数，也可以写 5m / 1h30m / 1天2小时 这类时长文本',
    '  type            chat 聊天框 / title 大标题 / actionbar 物品栏上方 / command 执行指令',
    '  message         聊天框文本；title 与 subtitle 用于大标题；command 用于 type 为 command 时',
    '  times           大标题的淡入、停留、淡出时间（秒），三个数字分别写在 fade_in / stay / fade_out 里',
    '  color           可选的聊天框颜色（yellow、#FFAA00 等），也可直接在文本里写 §e 之类的旧版颜色代码',
    '  sound           可选，例如 minecraft:block.note_block.pling',
    '  sound_source    音效频道，默认 master；1.12 及更早的服务端留空以省略该参数',
    '  sound_position  音效位置，默认 @s（每个玩家在自己位置听到，需要 1.13+）；',
    '                  也可以写坐标文本（如 ~ ~ ~ 或 100 64 100），或留空表示省略坐标与音量',
    '                  注意 playsound 的语法里坐标在音量前面，要自定义音量/音调就必须给坐标',
    '文本占位符：{remaining} {remaining_seconds} {remaining_minutes} {time} {date} {datetime} {schedule} {cron}',
    '重启方式 restart_method：mcdr_restart（默认，MCDR 重启服务器）/ stop（只停服）/ stop_exit（停服并退出 MCDR）',
    '                        / custom（执行 custom_command）/ none（只发提醒不重启）',
    '指令权限：list / next 所有玩家可用；status / history 需要 helper；其余变更指令需要 MCDR admin。',
    '  Minecraft 的 /op 不等于 MCDR admin，需要服主在控制台执行 !!MCDR permission set <玩家名> admin。',
    '自定义简化指令：在 command_alias 里写 !!sr，则 !!sr 与 !!srestart 等价。',
    '计划也可以用指令管理（会自动写回本文件）：',
    '  !!srestart list                          查看序号、cron、下次重启时间',
    '  !!srestart add <计划名> <cron>            新建计划，如 !!srestart add 每日重启 0 4 * * *',
    '  !!srestart enable|disable [<序号>|all]    打开/关闭计划',
    '  !!srestart remove <序号>                  删除计划',
    '  !!srestart test <序号>                    只发提醒不重启，用于预览',
]


def _default_notification(**kwargs) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        'enabled': True,
        'advance_time': 60,
        'type': 'chat',
        'message': '',
        'title': '',
        'subtitle': '',
        'times': {'fade_in': 1.0, 'stay': 4.0, 'fade_out': 1.0},
        'color': None,
        'sound': None,
        'sound_source': 'master',
        'sound_position': PER_PLAYER_POSITION,
        'sound_volume': 1.0,
        'sound_pitch': 1.0,
        'command': '',
    }
    base.update(kwargs)
    return base


DEFAULT_NOTIFICATIONS: List[Dict[str, Any]] = [
    _default_notification(
        advance_time=300,
        type='chat',
        message='§e[定时重启] §f服务器将在 §b{remaining} §f后重启（§b{time}§f）',
    ),
]


def default_schedule_entry(**kwargs) -> Dict[str, Any]:
    """一条计划的规范写法（``!!srestart add`` 新建计划时也用它）"""
    base: Dict[str, Any] = {
        'name': '示例计划',
        'enabled': False,
        'cron': '0 4 * * *',
        'restart_method': 'mcdr_restart',
        'custom_command': 'stop',
        'custom_auto_start': False,
        'restart_delay_seconds': 0,
        'kick_players': False,
        'kick_message': '服务器正在重启，请稍后重新连接',
        'use_default_notifications': True,
        'notifications': [],
    }
    base.update(kwargs)
    return base


DEFAULT_CONFIG: Dict[str, Any] = {
    'enabled': True,
    '_readme': DEFAULT_README,
    'timezone': None,
    'check_interval_seconds': 1.0,
    'skip_missed_notifications': True,
    'notify_on_join': True,
    'join_message': '§e[定时重启] §f服务器将在 §b{remaining} §f后重启（§b{time}§f）',
    'log_history': True,
    'history_size': 100,
    'command_alias': '',
    'default_notifications': DEFAULT_NOTIFICATIONS,
    'schedules': [
        default_schedule_entry(
            name='示例-每天凌晨4点重启（默认关闭，改 enabled 为 true 即生效）',
            enabled=False,
            cron='0 4 * * *',
        ),
    ],
}

#: 已废弃的顶层配置键，加载时会自动移除
DEPRECATED_CONFIG_KEYS: FrozenSet[str] = frozenset()


def _deep_merge_defaults(default: Any, current: Any) -> Tuple[Any, bool]:
    """把 ``default`` 的缺失键递归合并进 ``current``。

    :return: (合并后的值, 是否发生修改)
    """
    if isinstance(default, dict) and isinstance(current, dict):
        merged = dict(current)
        modified = False
        for key, default_value in default.items():
            if key not in merged:
                merged[key] = copy.deepcopy(default_value)
                modified = True
            else:
                merged_value, sub_modified = _deep_merge_defaults(default_value, merged[key])
                if sub_modified:
                    merged[key] = merged_value
                    modified = True
        return merged, modified
    # 列表/标量保持用户原值，避免覆盖用户自定义的提醒或计划
    return current, False


def upgrade_config(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """把旧版本配置文件升级到当前默认结构。

    行为：

    * 递归补齐缺失的顶层 / 嵌套字段（保留用户已有值）
    * ``_readme`` 总是更新为插件自带的最新说明
    * ``schedules`` 数组里的每个计划对象用 :func:`default_schedule_entry` 补齐字段
    * 移除 :data:`DEPRECATED_CONFIG_KEYS` 中标记的废弃键

    :return: (升级后的配置, 是否发生修改)
    """
    if not isinstance(raw, dict):
        return copy.deepcopy(DEFAULT_CONFIG), True

    default = copy.deepcopy(DEFAULT_CONFIG)
    upgraded, modified = _deep_merge_defaults(default, raw)

    # _readme 随插件版本更新
    if upgraded.get('_readme') != default['_readme']:
        upgraded['_readme'] = copy.deepcopy(default['_readme'])
        modified = True

    # schedules 中每个计划对象都要是插件认得的合法结构
    default_schedule = default_schedule_entry()
    schedules = upgraded.get('schedules')
    if isinstance(schedules, list):
        new_schedules: List[Any] = []
        schedules_modified = False
        for entry in schedules:
            if isinstance(entry, dict):
                merged_entry, entry_modified = _deep_merge_defaults(default_schedule, entry)
                new_schedules.append(merged_entry)
                if entry_modified:
                    schedules_modified = True
            else:
                # 非对象条目保留原样，让 Config.from_raw 报错误并跳过
                new_schedules.append(entry)
        if schedules_modified:
            upgraded['schedules'] = new_schedules
            modified = True

    # 清理废弃键
    for key in DEPRECATED_CONFIG_KEYS:
        if key in upgraded:
            del upgraded[key]
            modified = True

    return upgraded, modified


def config_file_path(server) -> str:
    """配置文件在 MCDR 数据目录里的完整路径"""
    return os.path.join(server.get_data_folder(), 'config.json')


def preprocess_config_file(server) -> None:
    """读取前处理配置文件，避免用户的计划被静默丢掉

    两种情况：

    * **文件带 UTF-8 BOM**（Windows 记事本、PowerShell ``Set-Content -Encoding UTF8``
      等都会写出 BOM）：MCDR 的 json 读取会失败并按 ``regen`` 策略直接把配置重置成默认值，
      用户的计划就没了。这里先就地去掉 BOM。
    * **文件内容无法解析**：先把原文件备份成 ``config.json.broken-<时间>``，
      这样即使 MCDR 随后用默认配置覆盖它，用户也能找回自己写的内容。
    """
    path = config_file_path(server)
    if not os.path.isfile(path):
        return

    logger = getattr(server, 'logger', None)
    try:
        with open(path, 'rb') as file_handler:
            content = file_handler.read()
    except OSError:
        return

    if content.startswith(codecs.BOM_UTF8):
        try:
            with open(path, 'w', encoding='utf8') as file_handler:
                file_handler.write(content[len(codecs.BOM_UTF8):].decode('utf8'))
            if logger is not None:
                logger.warning('[scheduled_restart] 配置文件带 UTF-8 BOM，已自动去掉（否则会导致配置被重置）')
        except (OSError, UnicodeDecodeError):
            pass
        try:
            with open(path, 'rb') as file_handler:
                content = file_handler.read()
        except OSError:
            return

    try:
        json.loads(content.decode('utf8'))
    except (ValueError, UnicodeDecodeError) as e:
        backup_path = f'{path}.broken-{datetime.now().strftime("%Y%m%d-%H%M%S")}'
        try:
            with open(backup_path, 'wb') as file_handler:
                file_handler.write(content)
        except OSError:  # pragma: no cover
            backup_path = '<备份失败>'
        if logger is not None:
            logger.error(
                f'[scheduled_restart] 配置文件无法解析（{e}），已备份到 {backup_path}；'
                f'接下来会按默认配置生成一份新的，请从备份里找回你的计划'
            )


def load_config(server) -> Config:
    """从 MCDR 数据目录读取配置文件并规范化

    缺失的顶层键由 MCDR 自动补齐；嵌套的默认值由 :func:`upgrade_config` 补齐。
    插件更新导致配置结构变化时，会自动把新增字段写进配置文件，
    并移除 :data:`DEPRECATED_CONFIG_KEYS` 中标记的废弃键。
    若配置文件本身存在错误（如 cron 写错），则不会回写，避免覆盖用户写错的内容。
    """
    preprocess_config_file(server)
    raw = server.load_config_simple(
        file_name='config.json',
        default_config=copy.deepcopy(DEFAULT_CONFIG),
        echo_in_console=True,
    )
    raw, upgraded = upgrade_config(raw)
    config = Config.from_raw(raw)
    if not config.errors and (upgraded or config.needs_save):
        try:
            server.save_config_simple(config.to_raw(), file_name='config.json')
        except Exception as e:  # pragma: no cover - 保存失败不影响运行
            server.logger.warning(f'[scheduled_restart] 保存规范化后的配置失败：{e}')
    return config
