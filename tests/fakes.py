"""测试用的假对象：假 MCDR 服务器接口、假命令源、假时钟。

只实现插件真正用到的那部分 :class:`~mcdreforged.plugin.si.server_interface.ServerInterface`
接口，用来在没有真实 Minecraft 服务端的情况下把插件完整跑一遍。
"""

from __future__ import annotations

import copy
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from mcdreforged.api.types import PermissionLevel
from mcdreforged.minecraft.rtext.text import RTextBase

from scheduled_restart.config import DEFAULT_CONFIG

#: 测试用的数据目录统一放在工程内（沙箱环境下系统临时目录可能不可写，
#: 且 tempfile.mkdtemp 以 0o700 创建目录，在受限沙箱下会变成不可访问，
#: 所以这里统一用 os.mkdir 的默认权限创建）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_TMP_ROOT = os.path.join(_PROJECT_ROOT, '.test-tmp')


def workspace_temp_dir(prefix: str = 'tmp-') -> str:
    """在工程内创建一个临时目录（默认权限），返回路径"""
    os.makedirs(TEST_TMP_ROOT, exist_ok=True)
    for _ in range(100):
        path = os.path.join(TEST_TMP_ROOT, prefix + uuid.uuid4().hex[:8])
        try:
            os.mkdir(path)
        except FileExistsError:
            continue
        else:
            return path
    raise RuntimeError(f'无法在 {TEST_TMP_ROOT} 下创建临时目录')


class FakeServer:
    """模拟 PluginServerInterface 中插件用到的部分"""

    def __init__(
            self,
            config_raw: Optional[Dict[str, Any]] = None,
            *,
            running: bool = True,
            data_folder: Optional[str] = None,
    ):
        self.logger = logging.getLogger('fake-mcdr')
        self.running = running
        self.config_raw = copy.deepcopy(DEFAULT_CONFIG if config_raw is None else config_raw)
        self.data_folder = data_folder if data_folder is not None else workspace_temp_dir('scheduled-restart-test-')

        #: ``execute`` 下发给服务端的指令
        self.executed: List[str] = []
        #: ``say`` 广播的富文本
        self.said: List[Any] = []
        #: ``tell`` 私聊的 (玩家, 富文本)
        self.told: List[Tuple[str, Any]] = []

        self.restart_calls = 0
        self.stop_calls = 0
        self.stop_exit_calls = 0
        self.start_calls = 0
        self.wait_for_start_calls = 0

        self.registered_commands: List[Any] = []
        self.help_messages: List[Tuple[str, Any, Optional[int]]] = []
        self.saved_config: Optional[Dict[str, Any]] = None
        self.loaded_config_file_name: Optional[str] = None

    # ------------------------------------------------------------ 配置相关

    def get_data_folder(self) -> str:
        return self.data_folder

    @property
    def config_file_path(self) -> str:
        return os.path.join(self.data_folder, 'config.json')

    def seed_config_file(self, raw: Optional[Dict[str, Any]] = None) -> str:
        """把配置写进数据目录里的 config.json（模拟 MCDR 的落盘行为）"""
        import json
        with open(self.config_file_path, 'w', encoding='utf8') as file_handler:
            json.dump(self.config_raw if raw is None else raw, file_handler, ensure_ascii=False, indent=4)
        return self.config_file_path

    def load_config_simple(self, file_name: Optional[str] = None, default_config: Optional[dict] = None, **kwargs):
        """模拟 MCDR：优先读数据目录里的配置文件（解析失败时按 regen 策略回退），
        文件里缺的顶层键用默认值补齐"""
        import json
        self.loaded_config_file_name = file_name
        data = None
        if os.path.isfile(self.config_file_path):
            try:
                with open(self.config_file_path, encoding='utf-8-sig') as file_handler:
                    data = json.load(file_handler)
                if not isinstance(data, dict):
                    data = None
            except ValueError:
                data = None      # 模拟 MCDR 的 failure_policy='regen'
        if data is None:
            data = copy.deepcopy(self.config_raw)
        for key, value in (default_config or {}).items():
            data.setdefault(key, copy.deepcopy(value))
        return data

    def save_config_simple(self, config, file_name: Optional[str] = None, **kwargs) -> None:
        self.saved_config = copy.deepcopy(config)

    # ------------------------------------------------------------ 服务端交互

    def execute(self, text: str, **kwargs) -> None:
        self.executed.append(text)

    def say(self, text, **kwargs) -> None:
        self.said.append(text)

    def tell(self, player: str, text, **kwargs) -> None:
        self.told.append((player, text))

    def is_server_running(self) -> bool:
        return self.running

    def restart(self) -> bool:
        self.restart_calls += 1
        return True

    def stop(self) -> bool:
        self.stop_calls += 1
        return True

    def stop_exit(self) -> bool:
        self.stop_exit_calls += 1
        return True

    def start(self) -> bool:
        self.start_calls += 1
        return True

    def wait_for_start(self) -> None:
        self.wait_for_start_calls += 1

    # ------------------------------------------------------------ 插件注册

    def register_command(self, node) -> None:
        self.registered_commands.append(node)

    def register_help_message(self, prefix: str, message, permission: Optional[int] = None) -> None:
        self.help_messages.append((prefix, message, permission))


class FakeCommandSource:
    """模拟 CommandSource，用于把真实指令树跑起来"""

    def __init__(
            self,
            permission: int = PermissionLevel.OWNER,
            *,
            console: bool = True,
            player: str = '',
            server: Optional[FakeServer] = None,
    ):
        self.permission = permission
        self.console = console
        self.player = player
        self.server = server
        self.replies: List[Any] = []

    # ---- CommandSource 协议 ----

    @property
    def is_player(self) -> bool:
        return not self.console

    @property
    def is_console(self) -> bool:
        return self.console

    def get_permission_level(self) -> int:
        return self.permission

    def has_permission(self, level: int) -> bool:
        return self.permission >= level

    def has_permission_higher_than(self, level: int) -> bool:
        return self.permission > level

    def reply(self, message, **kwargs) -> None:
        self.replies.append(message)

    def get_server(self):
        return self.server

    def __str__(self) -> str:
        return 'Console' if self.console else f'Player[{self.player}]'

    # ---- 测试辅助 ----

    def text(self) -> str:
        """所有回复拼接成纯文本"""
        return '\n'.join(plain_text(item) for item in self.replies)


class FakeClock:
    """可控时钟；默认返回不带时区的时间，与无时区配置的行为一致"""

    def __init__(self, moment: datetime):
        self._now = moment

    def now(self) -> datetime:
        return self._now

    def timezone(self):
        return None

    def timezone_name(self) -> Optional[str]:
        return None

    def set(self, moment: datetime) -> None:
        self._now = moment

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


def plain_text(message) -> str:
    """把 str / RText 统一成纯文本，方便断言"""
    if isinstance(message, RTextBase):
        return message.to_plain_text()
    return str(message)


def run_command(root, source: FakeCommandSource, command: str) -> int:
    """用 MCDR 真正的指令解析器执行一条指令，返回被触发的回调个数"""
    from mcdreforged.command.builder.callback import DirectCallbackInvoker

    executions = root._entry_execute(source, command)  # noqa: SLF001 - 测试里直接用内部入口
    for execution in executions:
        execution.scheduled_callback.invoke(DirectCallbackInvoker())
    return len(executions)


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0, interval: float = 0.01) -> bool:
    """等待异步工作线程完成"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def read_history_lines(server: FakeServer) -> List[str]:
    path = os.path.join(server.data_folder, 'history.jsonl')
    if not os.path.isfile(path):
        return []
    with open(path, encoding='utf8') as file_handler:
        return [line for line in file_handler.read().splitlines() if line.strip()]
