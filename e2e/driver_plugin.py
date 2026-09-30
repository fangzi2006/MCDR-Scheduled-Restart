"""端到端测试驱动插件（solo plugin，仅用于 e2e 测试环境，不属于交付的插件本体）。

在服务器启动后，通过 ``server.execute_command`` 在**真实 MCDR** 里依次执行一串
``!!srestart`` 指令（真实解析、真实权限、真实回调），并在每步之后打印
``config/scheduled_restart/config.json`` 的快照，用来验证：

* ``add`` 真的写进了配置文件、并且立刻参与调度
* ``disable`` / ``enable`` 改的是配置文件（含"配置文件里本来就关闭"的计划）
* ``remove`` 真的删掉了对应条目
* 序号寻址（1 起）与配置文件数组位置一一对应
"""

import json
import os
import time

from mcdreforged.api.all import new_thread

PLUGIN_CONFIG_PATH = os.path.join('config', 'scheduled_restart', 'config.json')
SNAPSHOT_PATH = os.path.join('server', 'e2e_snapshots.log')
COMMAND_FILE_PATH = os.path.join('server', 'e2e_commands.txt')

COMMANDS = [
    '!!srestart list',
    '!!srestart add 凌晨测试 0 7 * * *',
    '!!srestart add 名字重复 0 8 * * *',
    '!!srestart add 名字重复 0 9 * * *',
    '!!srestart add 坏计划 99 * * * *',
    '!!srestart disable 1',
    '!!srestart reload',           # 让内存配置与文件同步：此时 1 号在配置文件里就是关闭的
    '!!srestart list',
    '!!srestart enable 1',         # 打开一个「配置文件里本来就关闭」的计划
    '!!srestart test 2',
    '!!srestart remove 2',
    '!!srestart list',
]

_STEP_DELAY = 2.0
_done = False


def _commands() -> list:
    """优先用 server/e2e_commands.txt 里的指令（每行一条），方便按场景切换"""
    if os.path.isfile(COMMAND_FILE_PATH):
        with open(COMMAND_FILE_PATH, encoding='utf8') as file_handler:
            lines = [line.strip() for line in file_handler if line.strip() and not line.startswith('#')]
        if lines:
            return lines
    return COMMANDS


def _snapshot(server) -> str:
    if not os.path.isfile(PLUGIN_CONFIG_PATH):
        return '<配置文件不存在>'
    try:
        with open(PLUGIN_CONFIG_PATH, encoding='utf8') as file_handler:
            raw = json.load(file_handler)
        items = [
            (entry.get('name'), entry.get('enabled'))
            for entry in raw.get('schedules', []) if isinstance(entry, dict)
        ]
        return str(items)
    except Exception as e:  # pragma: no cover
        return f'<读取失败 {e}>'


def _record(text: str) -> None:
    """把快照写进 UTF-8 文件，避免控制台重定向的编码问题"""
    try:
        with open(SNAPSHOT_PATH, 'a', encoding='utf8') as file_handler:
            file_handler.write(text + '\n')
    except Exception:  # pragma: no cover
        pass


def _drive(server) -> None:
    global _done
    time.sleep(2)
    if _done:
        return
    _done = True

    _record('===== 端到端指令测试开始 =====')
    _record(f'初始配置: {_snapshot(server)}')
    source = server.get_console_command_source()
    commands = _commands()

    for index, command in enumerate(commands, start=1):
        _record(f'>>> ({index}/{len(commands)}) {command}')
        try:
            server.execute_command(command, source)
        except Exception as e:
            _record(f'    指令执行异常: {e}')
        time.sleep(_STEP_DELAY)
        _record(f'    配置快照: {_snapshot(server)}')

    _record('===== 端到端指令测试结束 =====')


def on_server_startup(server):
    _drive_thread = new_thread('e2e-driver')(_drive)
    _drive_thread(server)
