"""提醒内容的渲染与发送：聊天框、大标题（title/subtitle）、物品栏提示、指令。

* 聊天框使用 MCDR 的 ``server.say`` / ``server.tell``（内部使用 tellraw，兼容各服务端）
* 大标题使用 ``title @a times/subtitle/title`` 指令，文本走 JSON 文本组件，颜色安全
* 文本支持 ``§`` 旧版颜色代码，也支持 ``color`` 字段（颜色名或 ``#RRGGBB``）
* 文本支持占位符，见 :func:`build_context`
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from mcdreforged.api.rtext import RColor, RStyle, RText, RTextBase, RTextList

from .config import Notification
from .cron import format_duration_cn

__all__ = [
    'Notifier',
    'build_context',
    'render',
    'split_legacy',
    'to_rtext',
    'to_json_str',
]

_LEGACY_COLORS: Dict[str, str] = {
    '0': 'black',
    '1': 'dark_blue',
    '2': 'dark_green',
    '3': 'dark_aqua',
    '4': 'dark_red',
    '5': 'dark_purple',
    '6': 'gold',
    '7': 'gray',
    '8': 'dark_gray',
    '9': 'blue',
    'a': 'green',
    'b': 'aqua',
    'c': 'red',
    'd': 'light_purple',
    'e': 'yellow',
    'f': 'white',
}

_LEGACY_STYLES: Dict[str, str] = {
    'k': 'obfuscated',
    'l': 'bold',
    'm': 'strikethrough',
    'n': 'underlined',
    'o': 'italic',
}

_NAME_TO_LEGACY_CODE: Dict[str, str] = {name: code for code, name in _LEGACY_COLORS.items()}
_NAME_TO_LEGACY_CODE.update({'grey': '7', 'dark_grey': '8', 'purple': 'd', 'magenta': 'd', 'cyan': 'b', 'lime': 'a'})


# --------------------------------------------------------------------------- #
#                                 旧版颜色代码解析                              #
# --------------------------------------------------------------------------- #


@dataclass
class _Segment:
    text: str
    color: Optional[str] = None
    styles: Tuple[str, ...] = field(default_factory=tuple)


def split_legacy(text: str) -> List[_Segment]:
    """把带 ``§`` 颜色代码的文本拆成若干片段"""
    text = '' if text is None else str(text)
    segments: List[_Segment] = []
    buffer: List[str] = []
    color: Optional[str] = None
    styles: set = set()

    def flush() -> None:
        if buffer:
            segments.append(_Segment(''.join(buffer), color, tuple(sorted(styles))))
            buffer.clear()

    index = 0
    while index < len(text):
        char = text[index]
        if char == '§' and index + 1 < len(text):
            code = text[index + 1].lower()
            if code in _LEGACY_COLORS or code in _LEGACY_STYLES or code == 'r':
                flush()
                if code == 'r':
                    color, styles = None, set()
                elif code in _LEGACY_COLORS:
                    color, styles = _LEGACY_COLORS[code], set()
                else:
                    styles.add(_LEGACY_STYLES[code])
                index += 2
                continue
        buffer.append(char)
        index += 1

    flush()
    if not segments:
        segments.append(_Segment('', None, ()))
    return segments


def _color_prefix(color: Optional[str]) -> str:
    """把颜色名转换成 ``§`` 前缀；``#RRGGBB`` 由 :func:`_rgb_color` 处理"""
    if not color:
        return ''
    text = color.strip()
    if text == '':
        return ''
    if text.startswith('§'):
        return text
    lowered = text.lower()
    if lowered in _NAME_TO_LEGACY_CODE:
        return '§' + _NAME_TO_LEGACY_CODE[lowered]
    return ''


def _rgb_color(color: Optional[str]) -> Optional[RColor]:
    if not color:
        return None
    text = color.strip()
    if text.lower().startswith('0x'):
        text = '#' + text[2:]
    if not text.startswith('#') or len(text) != 7:
        return None
    try:
        return RColor.from_mc_value(text)
    except Exception:
        return None


def to_rtext(text: str, color: Optional[str] = None) -> RTextBase:
    """把文本（可含 ``§`` 颜色代码）转换成 MCDR 的富文本对象"""
    content = _color_prefix(color) + ('' if text is None else str(text))
    segments = split_legacy(content)
    rgb = _rgb_color(color)

    parts: List[RText] = []
    for segment in segments:
        part = RText(segment.text)
        if segment.color is not None:
            part.set_color(RColor.from_mc_value(segment.color))
        elif rgb is not None:
            part.set_color(rgb)
        if segment.styles:
            part.set_styles([getattr(RStyle, name) for name in segment.styles])
        parts.append(part)

    if len(parts) == 1:
        return parts[0]
    return RTextList(*parts)


def to_json_str(text: str, color: Optional[str] = None) -> str:
    """把文本转换成 Minecraft JSON 文本组件字符串（用于 title 指令）"""
    return json.dumps(to_rtext(text, color).to_json_object(), ensure_ascii=False)


# --------------------------------------------------------------------------- #
#                                    模板渲染                                   #
# --------------------------------------------------------------------------- #


def render(text: str, context: Dict[str, Any]) -> str:
    """把 ``{key}`` 占位符替换成上下文中的值（未知占位符原样保留）"""
    if text is None:
        return ''
    result = str(text)
    for key, value in context.items():
        result = result.replace('{' + key + '}', '' if value is None else str(value))
    return result


def build_context(
        *,
        schedule_name: str = '',
        cron_expression: str = '',
        restart_time: Optional[datetime] = None,
        now: Optional[datetime] = None,
        advance_seconds: Optional[float] = None,
        index: Optional[int] = None,
        total: Optional[int] = None,
) -> Dict[str, Any]:
    """构造模板上下文

    :param advance_seconds: 直接指定「剩余秒数」（用于 ``!!srestart test`` 预览）
    """
    if advance_seconds is not None:
        remaining = max(0.0, float(advance_seconds))
    elif restart_time is not None and now is not None:
        remaining = max(0.0, (restart_time - now).total_seconds())
    else:
        remaining = 0.0

    moment = restart_time
    context: Dict[str, Any] = {
        'schedule': schedule_name,
        'cron': cron_expression,
        'remaining': format_duration_cn(remaining),
        'remaining_seconds': int(round(remaining)),
        'remaining_minutes': int(math.ceil(remaining / 60.0)),
        'time': moment.strftime('%H:%M:%S') if moment else '',
        'date': moment.strftime('%Y-%m-%d') if moment else '',
        'datetime': moment.strftime('%Y-%m-%d %H:%M:%S') if moment else '',
        'index': '' if index is None else index,
        'total': '' if total is None else total,
    }
    return context


def _to_ticks(seconds: float) -> int:
    return max(0, int(round(float(seconds) * 20)))


# --------------------------------------------------------------------------- #
#                                     发送器                                    #
# --------------------------------------------------------------------------- #


class Notifier:
    """把 :class:`~scheduled_restart.config.Notification` 发送到游戏内"""

    def __init__(self, server):
        self._server = server

    # ------------------------------------------------------------ 单条提醒

    def send_notification(self, notification: Notification, context: Dict[str, Any]) -> None:
        if notification.type == 'chat':
            self.broadcast_chat(render(notification.message, context), notification.color)
        elif notification.type == 'title':
            self.broadcast_title(
                render(notification.title, context),
                render(notification.subtitle, context),
                notification,
            )
        elif notification.type == 'actionbar':
            text = notification.message or notification.title
            self.broadcast_actionbar(render(text, context), notification.color)
        elif notification.type == 'command':
            self.execute(render(notification.command, context))

        if notification.sound:
            self.play_sound(
                notification.sound,
                source=notification.sound_source,
                volume=notification.sound_volume,
                pitch=notification.sound_pitch,
            )

    # ------------------------------------------------------------ 基础动作

    def broadcast_chat(self, text: str, color: Optional[str] = None) -> None:
        self._server.say(to_rtext(text, color))

    def tell_chat(self, player: str, text: str, color: Optional[str] = None) -> None:
        self._server.tell(player, to_rtext(text, color))

    def broadcast_title(self, title: str, subtitle: str, notification: Notification) -> None:
        times = '{} {} {}'.format(
            _to_ticks(notification.fade_in),
            _to_ticks(notification.stay),
            _to_ticks(notification.fade_out),
        )
        self._server.execute(f'title @a times {times}')
        if subtitle.strip() != '':
            self._server.execute('title @a subtitle ' + to_json_str(subtitle))
        if title.strip() != '':
            self._server.execute('title @a title ' + to_json_str(title))

    def broadcast_actionbar(self, text: str, color: Optional[str] = None) -> None:
        self._server.execute('title @a actionbar ' + to_json_str(text, color))

    def play_sound(self, sound: str, *, source: str = 'master', volume: float = 1.0, pitch: float = 1.0) -> None:
        parts = ['playsound', sound]
        if source:
            parts.append(source)
        parts.append('@a')
        parts.append(f'{float(volume):g}')
        parts.append(f'{float(pitch):g}')
        self._server.execute(' '.join(parts))

    def execute(self, command: str) -> None:
        self._server.execute(command)
