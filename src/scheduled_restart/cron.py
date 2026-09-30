"""Linux cron 表达式解析与「下次触发时间」计算。

本模块不依赖任何第三方库，行为对齐 vixie-cron / cronie：

* 5 个字段：``分 时 日 月 周``
* 6 个字段：``秒 分 时 日 月 周``（Quartz 风格扩展，秒为第一位）

字段语法：

* ``*`` / ``?``：任意值
* ``5``：具体值
* ``1-10``：范围
* ``1,3,5``：列表
* ``*/5``：步长
* ``1-10/2``：范围内步长
* ``5/10``：从 5 开始、步长 10 直到字段最大值（cronie 扩展）
* 月份支持 ``JAN``~``DEC``，星期支持 ``SUN``~``SAT``、``0`` 与 ``7`` 均表示周日
* ``@yearly`` / ``@monthly`` / ``@weekly`` / ``@daily`` / ``@midnight`` / ``@hourly`` /
  ``@minutely``（``@every_minute``）宏

「日」与「周」同时被限定时，按 cron 惯例取 **或** 关系（满足任一即触发）。
时间字段以「本地墙钟时间」解释，时区由调用方（调度器）决定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, FrozenSet, List, Optional, Tuple

__all__ = [
    'CronError',
    'CronSchedule',
    'parse_cron',
    'format_duration_cn',
]

# 最远向后搜索 8 年，足以覆盖 2 月 29 日等罕见组合
_MAX_SEARCH_DAYS = 366 * 8

_MONTH_ALIASES: Dict[str, int] = {
    'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12,
}

_WEEKDAY_ALIASES: Dict[str, int] = {
    'SUN': 0, 'MON': 1, 'TUE': 2, 'WED': 3, 'THU': 4, 'FRI': 5, 'SAT': 6,
}

_WEEKDAY_CN = ('日', '一', '二', '三', '四', '五', '六')

_MACROS: Dict[str, str] = {
    '@yearly': '0 0 1 1 *',
    '@annually': '0 0 1 1 *',
    '@monthly': '0 0 1 * *',
    '@weekly': '0 0 * * 0',
    '@daily': '0 0 * * *',
    '@midnight': '0 0 * * *',
    '@hourly': '0 * * * *',
    '@minutely': '* * * * *',
    '@every_minute': '* * * * *',
}

_MACRO_DESC: Dict[str, str] = {
    '@yearly': '每年 1 月 1 日 00:00',
    '@annually': '每年 1 月 1 日 00:00',
    '@monthly': '每月 1 日 00:00',
    '@weekly': '每周日 00:00',
    '@daily': '每天 00:00',
    '@midnight': '每天 00:00',
    '@hourly': '每小时整点',
    '@minutely': '每分钟',
    '@every_minute': '每分钟',
}

_INT_PATTERN = re.compile(r'\d+')


class CronError(ValueError):
    """cron 表达式不合法（错误信息可直接展示给服主）"""


@dataclass(frozen=True)
class _FieldSpec:
    name: str
    min_value: int
    max_value: int
    aliases: Dict[str, int] = field(default_factory=dict)
    #: 允许等于该值的“溢出值”，并映射到 :attr:`overflow_to`（星期 7 -> 0）
    overflow: Optional[int] = None
    overflow_to: Optional[int] = None

    @property
    def range_hint(self) -> str:
        hint = f'[{self.min_value}, {self.max_value}]'
        if self.overflow is not None:
            hint += f' 或 {self.overflow}'
        if self.aliases:
            hint += '，或 ' + '/'.join(sorted(self.aliases.keys()))
        return hint


_SPEC_SECOND = _FieldSpec('秒', 0, 59)
_SPEC_MINUTE = _FieldSpec('分', 0, 59)
_SPEC_HOUR = _FieldSpec('时', 0, 23)
_SPEC_DAY = _FieldSpec('日', 1, 31)
_SPEC_MONTH = _FieldSpec('月', 1, 12, _MONTH_ALIASES)
_SPEC_WEEKDAY = _FieldSpec('周', 0, 6, _WEEKDAY_ALIASES, overflow=7, overflow_to=0)


def _parse_value(text: str, spec: _FieldSpec) -> int:
    token = text.strip().upper()
    if token in spec.aliases:
        return spec.aliases[token]
    if not _INT_PATTERN.fullmatch(token):
        raise CronError(f'{spec.name} 字段含非法值 {text!r}，可用值为 {spec.range_hint}')
    value = int(token)
    if spec.overflow is not None and value == spec.overflow:
        return spec.overflow_to  # type: ignore[return-value]
    if not (spec.min_value <= value <= spec.max_value):
        raise CronError(f'{spec.name} 字段的值 {value} 超出范围，可用值为 {spec.range_hint}')
    return value


def _parse_field(text: str, spec: _FieldSpec) -> Tuple[FrozenSet[int], bool]:
    """解析单个字段。

    :return: (允许的取值集合, 该字段是否为不带任何限定的 ``*`` / ``?``)
    """
    raw = text.strip()
    if raw == '':
        raise CronError(f'{spec.name} 字段为空')
    if raw in ('*', '?'):
        return frozenset(range(spec.min_value, spec.max_value + 1)), True

    values: set = set()
    for item in raw.split(','):
        item = item.strip()
        if item == '':
            raise CronError(f'{spec.name} 字段的列表中存在空项: {raw!r}')
        base, sep, step_text = item.partition('/')
        if sep:
            step_token = step_text.strip()
            if not _INT_PATTERN.fullmatch(step_token):
                raise CronError(f'{spec.name} 字段的步长必须是正整数: {item!r}')
            step = int(step_token)
            if step <= 0:
                raise CronError(f'{spec.name} 字段的步长必须大于 0: {item!r}')
        else:
            step = 1

        base = base.strip()
        if base in ('*', '?'):
            lower, upper = spec.min_value, spec.max_value
        else:
            lower_text, dash, upper_text = base.partition('-')
            if dash:
                if upper_text.strip() == '':
                    raise CronError(f'{spec.name} 字段的范围缺少结束值: {item!r}')
                lower = _parse_value(lower_text, spec)
                upper = _parse_value(upper_text, spec)
                if lower > upper:
                    raise CronError(f'{spec.name} 字段的范围起始值大于结束值: {item!r}')
            else:
                lower = _parse_value(base, spec)
                # ``a/n``：从 a 开始按步长 n 直到字段最大值
                upper = spec.max_value if sep else lower
        values.update(range(lower, upper + 1, step))

    if len(values) == 0:
        raise CronError(f'{spec.name} 字段没有解析出任何有效值: {raw!r}')
    return frozenset(values), False


@dataclass(frozen=True)
class CronSchedule:
    """一个已解析的 cron 表达式"""

    expression: str
    """用户书写的原始表达式"""

    has_seconds: bool
    macro: Optional[str]
    field_texts: Tuple[str, str, str, str, str, str]
    """归一化后的 6 个字段原文：秒 分 时 日 月 周"""

    seconds: FrozenSet[int]
    minutes: FrozenSet[int]
    hours: FrozenSet[int]
    days: FrozenSet[int]
    months: FrozenSet[int]
    weekdays: FrozenSet[int]

    seconds_is_star: bool
    minutes_is_star: bool
    hours_is_star: bool
    dom_is_star: bool
    months_is_star: bool
    dow_is_star: bool

    # ------------------------------------------------------------------ 匹配

    def day_matches(self, day: date) -> bool:
        """判断某一天是否满足「日 / 月 / 周」的约束"""
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.days
        # python: Monday=0..Sunday=6，cron: Sunday=0..Saturday=6
        dow_ok = ((day.weekday() + 1) % 7) in self.weekdays
        if self.dom_is_star:
            return dow_ok
        if self.dow_is_star:
            return dom_ok
        # 两者都被限定时按 cron 惯例取“或”
        return dom_ok or dow_ok

    def matches(self, moment: datetime, *, ignore_seconds: bool = False) -> bool:
        """判断给定时刻是否命中该表达式"""
        if not self.day_matches(moment.date()):
            return False
        if moment.hour not in self.hours or moment.minute not in self.minutes:
            return False
        if ignore_seconds:
            return True
        return moment.second in self.seconds

    # ------------------------------------------------------------ 下次触发时间

    def next_after(self, after: datetime) -> Optional[datetime]:
        """返回严格晚于 ``after`` 的下一个触发时刻，找不到则返回 ``None``

        返回值与 ``after`` 保持同样的「是否带时区」以及时区信息；
        日期时间按墙钟字段比较（夏令时跳变时以当天的墙钟时间为准）。
        """
        tzinfo = after.tzinfo
        base = after.replace(microsecond=0) + timedelta(seconds=1)
        base_date = base.date()
        base_hour, base_minute, base_second = base.hour, base.minute, base.second

        hours = sorted(self.hours)
        minutes = sorted(self.minutes)
        seconds = sorted(self.seconds)

        day = base_date
        for _ in range(_MAX_SEARCH_DAYS):
            if self.day_matches(day):
                for hour in hours:
                    if day == base_date and hour < base_hour:
                        continue
                    for minute in minutes:
                        if day == base_date and hour == base_hour and minute < base_minute:
                            continue
                        for second in seconds:
                            if day == base_date and hour == base_hour and minute == base_minute and second < base_second:
                                continue
                            return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=tzinfo)
            day += timedelta(days=1)
        return None

    def next_after_timestamp(self, after: datetime) -> Optional[float]:
        """便利方法：返回下次触发时间的时间戳"""
        moment = self.next_after(after)
        return None if moment is None else moment.timestamp()

    # ------------------------------------------------------------------ 描述

    def describe(self) -> str:
        """生成人类可读的中文描述，用于 ``!!srestart list``"""
        if self.macro is not None and self.macro in _MACRO_DESC:
            return _MACRO_DESC[self.macro]
        try:
            return self._describe_fields()
        except Exception:  # pragma: no cover - 描述失败不应影响主流程
            return f'cron {self.expression}'

    def _describe_fields(self) -> str:
        seconds = sorted(self.seconds)
        minutes = sorted(self.minutes)
        hours = sorted(self.hours)
        all_minutes = len(minutes) == 60
        all_hours = len(hours) == 24

        # ---- 时间部分 ----
        minute_field = self.field_texts[1]
        if minute_field.startswith('*/') and all_hours:
            time_desc = f'每 {minute_field[2:]} 分钟'
        elif minute_field.startswith('*/'):
            time_desc = f'每 {minute_field[2:]} 分钟'
        elif all_hours and all_minutes:
            time_desc = '每分钟'
        elif all_hours:
            time_desc = '每小时的第 ' + '、'.join(str(m) for m in minutes) + ' 分'
        elif len(hours) == 1 and len(minutes) == 1:
            time_desc = f'{hours[0]:02d}:{minutes[0]:02d}'
            if self.has_seconds and seconds != [0]:
                time_desc += ''.join(f':{s:02d}' for s in seconds)
        else:
            time_desc = '、'.join(f'{h:02d}:{m:02d}' for h in hours for m in minutes)

        # ---- 日期部分 ----
        if self.months_is_star:
            month_prefix = ''
        else:
            month_prefix = '每年 ' + '、'.join(f'{m} 月' for m in sorted(self.months)) + ' '

        weekdays = sorted(self.weekdays)
        if self.dom_is_star and self.dow_is_star:
            day_desc = '每天'
        elif self.dom_is_star:
            if len(weekdays) >= 7:
                day_desc = '每天'
            else:
                day_desc = '每周' + '、'.join(_WEEKDAY_CN[w] for w in weekdays)
        elif self.dow_is_star:
            days = sorted(self.days)
            if len(days) >= 31:
                day_desc = '每天'
            else:
                day_desc = '每月 ' + '、'.join(str(d) for d in days) + ' 日'
        else:
            days = sorted(self.days)
            day_desc = ('每月 ' + '、'.join(str(d) for d in days) + ' 日或每周'
                        + '、'.join(_WEEKDAY_CN[w] for w in weekdays))

        if month_prefix:
            if day_desc == '每天':
                day_desc = month_prefix.rstrip() + '每天'
            elif day_desc.startswith('每月 '):
                day_desc = month_prefix + day_desc[len('每月 '):]
            else:
                day_desc = month_prefix + day_desc

        if day_desc == '每天' and time_desc.startswith('每'):
            return time_desc
        return f'{day_desc} {time_desc}'

    def __str__(self) -> str:  # pragma: no cover - 便于调试
        return f'<CronSchedule {self.expression!r}>'


def parse_cron(expression: str) -> CronSchedule:
    """解析 cron 表达式，失败时抛出 :class:`CronError`"""
    if not isinstance(expression, str):
        raise CronError(f'cron 表达式必须是字符串，而不是 {type(expression).__name__}')
    raw = expression.strip()
    if raw == '':
        raise CronError('cron 表达式不能为空')

    macro: Optional[str] = None
    lowered = raw.lower()
    if lowered.startswith('@'):
        if lowered in _MACROS:
            macro = lowered
            field_texts = _MACROS[lowered].split()
            has_seconds = False
        elif lowered == '@reboot':
            raise CronError('不支持 @reboot：MCDR 进程本身不会被 cron 重启，请改用具体时间')
        else:
            supported = '、'.join(sorted(_MACROS.keys()))
            raise CronError(f'不支持的 cron 宏 {raw!r}，可用宏：{supported}')
    else:
        field_texts = raw.split()
        if len(field_texts) == 5:
            has_seconds = False
        elif len(field_texts) == 6:
            has_seconds = True
        else:
            raise CronError(
                f'cron 表达式需要 5 个字段（分 时 日 月 周）或 6 个字段（秒 分 时 日 月 周），'
                f'当前为 {len(field_texts)} 个字段: {raw!r}'
            )

    if has_seconds:
        second_text, minute_text, hour_text, day_text, month_text, weekday_text = field_texts
    else:
        second_text = '0'
        minute_text, hour_text, day_text, month_text, weekday_text = field_texts

    seconds, seconds_is_star = _parse_field(second_text, _SPEC_SECOND)
    minutes, minutes_is_star = _parse_field(minute_text, _SPEC_MINUTE)
    hours, hours_is_star = _parse_field(hour_text, _SPEC_HOUR)
    days, dom_is_star = _parse_field(day_text, _SPEC_DAY)
    months, months_is_star = _parse_field(month_text, _SPEC_MONTH)
    weekdays, dow_is_star = _parse_field(weekday_text, _SPEC_WEEKDAY)

    return CronSchedule(
        expression=raw,
        has_seconds=has_seconds,
        macro=macro,
        field_texts=(second_text, minute_text, hour_text, day_text, month_text, weekday_text),
        seconds=seconds,
        minutes=minutes,
        hours=hours,
        days=days,
        months=months,
        weekdays=weekdays,
        seconds_is_star=seconds_is_star,
        minutes_is_star=minutes_is_star,
        hours_is_star=hours_is_star,
        dom_is_star=dom_is_star,
        months_is_star=months_is_star,
        dow_is_star=dow_is_star,
    )


def format_duration_cn(seconds: float) -> str:
    """把秒数格式化成中文时长，例如 ``1小时5分3秒``"""
    total = int(round(max(0.0, float(seconds))))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts: List[str] = []
    if days:
        parts.append(f'{days}天')
    if hours:
        parts.append(f'{hours}小时')
    if minutes:
        parts.append(f'{minutes}分')
    if secs or not parts:
        parts.append(f'{secs}秒')
    return ''.join(parts)


_DURATION_UNITS = {
    'd': 86400, 'day': 86400, 'days': 86400, '天': 86400,
    'h': 3600, 'hr': 3600, 'hour': 3600, 'hours': 3600, '小时': 3600, '时': 3600,
    'm': 60, 'min': 60, 'minute': 60, 'minutes': 60, '分': 60, '分钟': 60,
    's': 1, 'sec': 1, 'second': 1, 'seconds': 1, '秒': 1,
}


def parse_duration(value, *, field_name: str = '时长') -> float:
    """解析时长，支持数字秒或 ``5m`` / ``1h30m`` / ``1天2小时`` 之类的字符串

    :raise CronError: 无法解析
    """
    if isinstance(value, bool):
        raise CronError(f'{field_name} 不能是布尔值')
    if isinstance(value, (int, float)):
        if value < 0:
            raise CronError(f'{field_name} 不能为负数: {value}')
        return float(value)
    if not isinstance(value, str):
        raise CronError(f'{field_name} 必须是数字或字符串，而不是 {type(value).__name__}')

    text = value.strip().lower().replace(' ', '')
    if text == '':
        raise CronError(f'{field_name} 不能为空')
    try:
        number = float(text)
    except ValueError:
        pass
    else:
        if number < 0:
            raise CronError(f'{field_name} 不能为负数: {value!r}')
        return number

    total = 0.0
    matched_length = 0
    for match in re.finditer(r'(\d+(?:\.\d+)?)([a-z\u4e00-\u9fff]+)', text):
        number_text, unit_text = match.group(1), match.group(2)
        if unit_text not in _DURATION_UNITS:
            raise CronError(f'{field_name} 含未知单位 {unit_text!r}: {value!r}')
        total += float(number_text) * _DURATION_UNITS[unit_text]
        matched_length += len(match.group(0))
    if matched_length != len(text) or matched_length == 0:
        raise CronError(f'{field_name} 格式无法识别: {value!r}（示例：90、5m、1h30m、1天2小时）')
    return total
