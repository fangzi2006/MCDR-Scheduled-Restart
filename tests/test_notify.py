"""提醒渲染与发送的测试"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from scheduled_restart.config import PER_PLAYER_POSITION, Notification
from scheduled_restart.notify import (
    Notifier,
    build_context,
    build_playsound_command,
    render,
    split_legacy,
    to_json_str,
    to_rtext,
)

from fakes import FakeServer, plain_text


# --------------------------------------------------------------------------- #
#                                旧版颜色代码                                   #
# --------------------------------------------------------------------------- #


def test_split_legacy_plain_text():
    segments = split_legacy('普通文本')
    assert len(segments) == 1
    assert segments[0].text == '普通文本'
    assert segments[0].color is None


def test_split_legacy_with_colors_and_styles():
    segments = split_legacy('§c红§l粗§r正常')
    assert [(item.text, item.color, item.styles) for item in segments] == [
        ('红', 'red', ()),
        ('粗', 'red', ('bold',)),
        ('正常', None, ()),
    ]


def test_color_code_resets_styles():
    """Minecraft 里颜色代码会清空之前的加粗等样式"""
    segments = split_legacy('§l粗§c红')
    assert [(item.text, item.color, item.styles) for item in segments] == [
        ('粗', None, ('bold',)),
        ('红', 'red', ()),
    ]


def test_invalid_color_code_is_kept_as_text():
    segments = split_legacy('§z未知')
    assert segments[0].text == '§z未知'


def test_split_legacy_empty():
    assert split_legacy('')[0].text == ''
    assert split_legacy(None)[0].text == ''


# --------------------------------------------------------------------------- #
#                                 JSON 组件                                     #
# --------------------------------------------------------------------------- #


def test_to_json_str_plain():
    assert to_json_str('你好') == '{"text": "你好"}'


def test_to_json_str_with_legacy_color():
    assert '"color": "red"' in to_json_str('§c警告')


def test_to_json_str_with_styles():
    output = to_json_str('§c红§l粗')
    assert output.count('"color": "red"') == 2
    assert '"bold": true' in output


def test_to_json_str_with_color_field():
    assert '"color": "yellow"' in to_json_str('提示', 'yellow')
    assert 'ffaa00' in to_json_str('提示', '#FFAA00').lower()
    assert '"color": "red"' in to_json_str('提示', '§c')


def test_to_rtext_plain_text_round_trip():
    assert plain_text(to_rtext('§a绿色文本')) == '绿色文本'


# --------------------------------------------------------------------------- #
#                                  模板渲染                                     #
# --------------------------------------------------------------------------- #


def test_render_placeholders():
    context = {'remaining': '5分', 'remaining_seconds': 300, 'schedule': '每日'}
    assert render('{schedule} 还有 {remaining}（{remaining_seconds} 秒）', context) == '每日 还有 5分（300 秒）'


def test_render_unknown_placeholder_kept():
    assert render('{unknown} 与 {remaining}', {'remaining': '1分'}) == '{unknown} 与 1分'


def test_build_context_from_times():
    now = datetime(2026, 1, 1, 3, 55, 0)
    context = build_context(
        schedule_name='每日',
        cron_expression='0 4 * * *',
        restart_time=datetime(2026, 1, 1, 4, 0, 0),
        now=now,
    )
    assert context['remaining'] == '5分'
    assert context['remaining_seconds'] == 300
    assert context['remaining_minutes'] == 5
    assert context['time'] == '04:00:00'
    assert context['date'] == '2026-01-01'
    assert context['datetime'] == '2026-01-01 04:00:00'
    assert context['schedule'] == '每日'
    assert context['cron'] == '0 4 * * *'


def test_build_context_from_advance_seconds():
    context = build_context(advance_seconds=90, restart_time=datetime(2026, 1, 1, 4, 0, 0))
    assert context['remaining'] == '1分30秒'
    assert context['remaining_seconds'] == 90
    assert context['remaining_minutes'] == 2


def test_build_context_remaining_never_negative():
    context = build_context(
        restart_time=datetime(2026, 1, 1, 4, 0, 0),
        now=datetime(2026, 1, 1, 4, 5, 0),
    )
    assert context['remaining'] == '0秒'


# --------------------------------------------------------------------------- #
#                                    发送                                       #
# --------------------------------------------------------------------------- #


def make_context(**kwargs):
    defaults = dict(restart_time=datetime(2026, 1, 1, 4, 0, 0), advance_seconds=60)
    defaults.update(kwargs)
    return build_context(**defaults)


def test_send_chat_notification():
    server = FakeServer()
    notifier = Notifier(server)
    notifier.send_notification(Notification(type='chat', message='§e服务器将在 {remaining} 后重启'), make_context())
    assert len(server.said) == 1
    assert plain_text(server.said[0]) == '服务器将在 1分 后重启'
    assert server.executed == []


def test_send_title_notification():
    server = FakeServer()
    notifier = Notifier(server)
    notification = Notification(
        type='title', title='重启倒计时', subtitle='剩余 {remaining}',
        fade_in=0.5, stay=4.0, fade_out=1.0,
    )
    notifier.send_notification(notification, make_context())
    assert server.executed == [
        'title @a times 10 80 20',
        'title @a subtitle {"text": "剩余 1分"}',
        'title @a title {"text": "重启倒计时"}',
    ]


def test_send_title_without_subtitle():
    server = FakeServer()
    notifier = Notifier(server)
    notifier.send_notification(Notification(type='title', title='T', subtitle=''), make_context())
    assert server.executed == ['title @a times 20 80 20', 'title @a title {"text": "T"}']


def test_send_actionbar_notification():
    server = FakeServer()
    notifier = Notifier(server)
    notifier.send_notification(Notification(type='actionbar', message='{remaining}'), make_context())
    assert len(server.executed) == 1
    assert server.executed[0].startswith('title @a actionbar ')


def test_send_command_notification():
    server = FakeServer()
    notifier = Notifier(server)
    notifier.send_notification(
        Notification(type='command', command='say 服务器将在 {remaining} 后重启'),
        make_context(),
    )
    assert server.executed == ['say 服务器将在 1分 后重启']


def test_send_notification_with_sound():
    """默认 sound_position="@s"：每个玩家在自己位置听到"""
    server = FakeServer()
    notifier = Notifier(server)
    notifier.send_notification(
        Notification(
            type='chat', message='hi', sound='minecraft:block.note_block.pling',
            sound_volume=0.5, sound_pitch=2.0,
        ),
        make_context(),
    )
    assert server.executed == [
        'execute as @a at @s run playsound minecraft:block.note_block.pling master @s ~ ~ ~ 0.5 2'
    ]


def test_send_notification_with_sound_without_source():
    server = FakeServer()
    notifier = Notifier(server)
    notifier.send_notification(
        Notification(type='chat', message='hi', sound='note.pling', sound_source=''),
        make_context(),
    )
    assert server.executed == ['execute as @a at @s run playsound note.pling @s ~ ~ ~ 1 1']


def test_send_notification_with_fixed_sound_position():
    """指定坐标时锚定在该位置播放（1.8+ 都能用的写法）"""
    server = FakeServer()
    notifier = Notifier(server)
    notifier.send_notification(
        Notification(
            type='chat', message='hi', sound='note.pling',
            sound_position='~ ~ ~', sound_volume=0.8, sound_pitch=1.2,
        ),
        make_context(),
    )
    assert server.executed == ['playsound note.pling master @a ~ ~ ~ 0.8 1.2']

    server.executed.clear()
    notifier.send_notification(
        Notification(type='chat', message='hi', sound='note.pling', sound_position='100 64 100'),
        make_context(),
    )
    assert server.executed == ['playsound note.pling master @a 100 64 100 1 1']


def test_send_notification_with_empty_sound_position():
    """sound_position 为空时连音量音调一起省略，绝不出现「音量被当成坐标」的写法"""
    server = FakeServer()
    notifier = Notifier(server)
    notifier.send_notification(
        Notification(type='chat', message='hi', sound='note.pling', sound_position=''),
        make_context(),
    )
    assert server.executed == ['playsound note.pling master @a']


@pytest.mark.parametrize('position,expected', [
    ('@s', 'execute as @a at @s run playsound sound.id master @s ~ ~ ~ 0.5 2'),
    ('~ ~ ~', 'playsound sound.id master @a ~ ~ ~ 0.5 2'),
    ('100 64 100', 'playsound sound.id master @a 100 64 100 0.5 2'),
    ('', 'playsound sound.id master @a'),
])
def test_build_playsound_command_shapes(position, expected):
    assert build_playsound_command('sound.id', 'master', 0.5, 2.0, position) == expected


def test_playsound_command_keeps_position_before_volume():
    """回归守卫：Java 的 playsound 语法是 ``… <目标> [<坐标>] [<音量>] [<音调>]``，
    坐标必须出现在音量之前，否则 ``… @a 1 1`` 会被当成坐标（需要 x y z 三个分量）而报错。"""

    def inner_tokens(command: str) -> list:
        return command.split('run ', 1)[-1].split()

    for position in ('@s', '~ ~ ~', '100 64 100'):
        tokens = inner_tokens(build_playsound_command('snd', 'master', 0.5, 2.0, position))
        assert tokens[0] == 'playsound'
        assert tokens[-2:] == ['0.5', '2'], tokens                    # 音量/音调在最后
        coordinates = tokens[-5:-2]                                   # 音量的前三个 token
        assert len(coordinates) == 3, tokens
        assert all(
            token == '~' or token.replace('-', '').replace('.', '').isdigit()
            for token in coordinates
        ), tokens                                                    # 它们必须是完整坐标
        assert tokens[-6] in ('@a', '@s'), tokens                     # 再前面是目标选择器

    # 没有坐标时，音量和音调必须一起省略（否则就会排到坐标的位置上）
    assert inner_tokens(build_playsound_command('snd', 'master', 0.5, 2.0, ''))[-1] == '@a'


def test_tell_chat():
    server = FakeServer()
    notifier = Notifier(server)
    notifier.tell_chat('Steve', '§e还有 {remaining}'.replace('{remaining}', '1分'))
    assert len(server.told) == 1
    player, message = server.told[0]
    assert player == 'Steve'
    assert plain_text(message) == '还有 1分'


def test_relative_time_context_uses_plan_time():
    """{time} 之类占位符应该来自真正的重启时刻"""
    server = FakeServer()
    notifier = Notifier(server)
    restart_time = datetime(2026, 3, 8, 4, 30, 0)
    context = build_context(restart_time=restart_time, now=restart_time - timedelta(minutes=30))
    notifier.send_notification(Notification(type='chat', message='将在 {time}（{datetime}）重启'), context)
    assert plain_text(server.said[0]) == '将在 04:30:00（2026-03-08 04:30:00）重启'
