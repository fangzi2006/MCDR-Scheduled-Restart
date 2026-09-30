"""cron 解析与下次触发时间计算的测试"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scheduled_restart.cron import (
    CronError,
    format_duration_cn,
    parse_cron,
    parse_duration,
)


# --------------------------------------------------------------------------- #
#                                   解析                                       #
# --------------------------------------------------------------------------- #


def test_parse_basic_five_fields():
    schedule = parse_cron('0 4 * * *')
    assert schedule.minutes == frozenset({0})
    assert schedule.hours == frozenset({4})
    assert schedule.has_seconds is False
    assert schedule.seconds == frozenset({0})
    assert schedule.dom_is_star and schedule.dow_is_star and schedule.months_is_star


def test_parse_lists_ranges_and_steps():
    schedule = parse_cron('*/15 9-17 1,15 * 1-5')
    assert schedule.minutes == frozenset({0, 15, 30, 45})
    assert schedule.hours == frozenset(range(9, 18))
    assert schedule.days == frozenset({1, 15})
    assert schedule.weekdays == frozenset({1, 2, 3, 4, 5})


def test_parse_question_mark_is_star():
    schedule = parse_cron('0 0 ? * ?')
    assert schedule.dom_is_star and schedule.dow_is_star


def test_parse_month_and_weekday_aliases():
    schedule = parse_cron('0 0 * JAN-MAR SUN')
    assert schedule.months == frozenset({1, 2, 3})
    assert schedule.weekdays == frozenset({0})


def test_parse_sunday_as_seven():
    assert parse_cron('0 0 * * 7').weekdays == frozenset({0})
    assert parse_cron('0 0 * * 0').weekdays == frozenset({0})


def test_parse_six_fields_with_seconds():
    schedule = parse_cron('30 0 4 * * *')
    assert schedule.has_seconds is True
    assert schedule.seconds == frozenset({30})
    assert schedule.minutes == frozenset({0})
    assert schedule.hours == frozenset({4})


def test_parse_step_from_value():
    # cronie 扩展：从 5 开始，步长 10
    assert parse_cron('5/10 * * * *').minutes == frozenset({5, 15, 25, 35, 45, 55})


def test_parse_macros():
    assert parse_cron('@daily').hours == frozenset({0})
    assert parse_cron('@hourly').minutes == frozenset({0})
    assert parse_cron('@weekly').weekdays == frozenset({0})
    assert parse_cron('@monthly').days == frozenset({1})
    assert parse_cron('@yearly').months == frozenset({1})
    assert parse_cron('@every_minute').minutes == frozenset(range(60))


@pytest.mark.parametrize('expression', [
    '',
    '   ',
    '* * * *',
    '* * * * * * *',
    '61 * * * *',
    '* 24 * * *',
    '* * 0 * *',
    '* * 32 * *',
    '* * * 13 *',
    '* * * * 8',
    '5-1 * * * *',
    '*/0 * * * *',
    'abc * * * *',
    '1,,2 * * * *',
    '@reboot',
    '@nonsense',
    '1- * * * *',
])
def test_parse_invalid(expression):
    with pytest.raises(CronError):
        parse_cron(expression)


def test_parse_error_message_is_helpful():
    with pytest.raises(CronError) as info:
        parse_cron('99 * * * *')
    message = str(info.value)
    assert '分' in message and '99' in message

    with pytest.raises(CronError) as info:
        parse_cron('* * * * * * *')
    assert '5 个字段' in str(info.value)


# --------------------------------------------------------------------------- #
#                                下次触发时间                                   #
# --------------------------------------------------------------------------- #


def test_next_after_daily():
    schedule = parse_cron('0 4 * * *')
    assert schedule.next_after(datetime(2026, 1, 1, 3, 0)) == datetime(2026, 1, 1, 4, 0)
    # 恰好命中时刻时要求严格晚于，所以顺延到第二天
    assert schedule.next_after(datetime(2026, 1, 1, 4, 0)) == datetime(2026, 1, 2, 4, 0)
    assert schedule.next_after(datetime(2026, 1, 1, 4, 0, 1)) == datetime(2026, 1, 2, 4, 0)


def test_next_after_every_fifteen_minutes():
    schedule = parse_cron('*/15 * * * *')
    assert schedule.next_after(datetime(2026, 1, 1, 10, 7)) == datetime(2026, 1, 1, 10, 15)
    assert schedule.next_after(datetime(2026, 1, 1, 10, 45)) == datetime(2026, 1, 1, 11, 0)


def test_next_after_weekly_monday():
    # 2024-01-01 是星期一
    schedule = parse_cron('30 3 * * 1')
    assert schedule.next_after(datetime(2024, 1, 1, 0, 0)) == datetime(2024, 1, 1, 3, 30)
    assert schedule.next_after(datetime(2024, 1, 1, 3, 30)) == datetime(2024, 1, 8, 3, 30)


def test_next_after_monthly():
    schedule = parse_cron('0 5 1 * *')
    assert schedule.next_after(datetime(2026, 1, 15, 0, 0)) == datetime(2026, 2, 1, 5, 0)


def test_next_after_leap_day():
    schedule = parse_cron('0 0 29 2 *')
    assert schedule.next_after(datetime(2023, 1, 1)) == datetime(2024, 2, 29, 0, 0)
    assert schedule.next_after(datetime(2024, 3, 1)) == datetime(2028, 2, 29, 0, 0)


def test_next_after_with_seconds():
    schedule = parse_cron('30 0 4 * * *')
    assert schedule.next_after(datetime(2026, 1, 1, 3, 59, 59)) == datetime(2026, 1, 1, 4, 0, 30)


def test_next_after_keeps_timezone():
    schedule = parse_cron('0 4 * * *')
    moment = schedule.next_after(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))
    assert moment.tzinfo is timezone.utc
    assert moment == datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc)


def test_day_of_month_and_weekday_are_or_related():
    """cron 惯例：日与周同时限定时取「或」"""
    schedule = parse_cron('0 0 1 * 1')
    # 2024-01-01 既是 1 号又是星期一
    assert schedule.matches(datetime(2024, 1, 1, 0, 0))
    # 2024-01-08 只是星期一
    assert schedule.matches(datetime(2024, 1, 8, 0, 0))
    # 2024-02-01 只是 1 号
    assert schedule.matches(datetime(2024, 2, 1, 0, 0))
    # 2024-01-09 既不是 1 号也不是星期一
    assert not schedule.matches(datetime(2024, 1, 9, 0, 0))


def test_matches():
    schedule = parse_cron('30 3 * * 1')
    assert schedule.matches(datetime(2024, 1, 1, 3, 30))
    assert not schedule.matches(datetime(2024, 1, 1, 3, 31))
    assert not schedule.matches(datetime(2024, 1, 2, 3, 30))


def test_next_after_is_fast_for_every_minute():
    schedule = parse_cron('* * * * *')
    moment = schedule.next_after(datetime(2026, 1, 1, 23, 59, 30))
    assert moment == datetime(2026, 1, 2, 0, 0)


# --------------------------------------------------------------------------- #
#                                   描述                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('expression,expected', [
    ('0 4 * * *', '每天 04:00'),
    ('30 3 * * 1', '每周一 03:30'),
    ('0 5 1 * *', '每月 1 日 05:00'),
    ('0 0 1 1 *', '每年 1 月 1 日 00:00'),
    ('*/10 * * * *', '每 10 分钟'),
    ('0 * * * *', '每小时的第 0 分'),
])
def test_describe(expression, expected):
    assert parse_cron(expression).describe() == expected


def test_describe_macro():
    assert parse_cron('@daily').describe() == '每天 00:00'


# --------------------------------------------------------------------------- #
#                                  时长工具                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize('seconds,expected', [
    (0, '0秒'),
    (59, '59秒'),
    (60, '1分'),
    (90, '1分30秒'),
    (3600, '1小时'),
    (3661, '1小时1分1秒'),
    (90061, '1天1小时1分1秒'),
])
def test_format_duration_cn(seconds, expected):
    assert format_duration_cn(seconds) == expected


@pytest.mark.parametrize('value,expected', [
    (90, 90.0),
    ('90', 90.0),
    ('5m', 300.0),
    ('1h30m', 5400.0),
    ('1天2小时', 93600.0),
    ('0.5m', 30.0),
    ('30s', 30.0),
])
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


@pytest.mark.parametrize('value', ['', 'abc', '5x', True, None, '-5', '1h30'])
def test_parse_duration_invalid(value):
    with pytest.raises(CronError):
        parse_duration(value)
