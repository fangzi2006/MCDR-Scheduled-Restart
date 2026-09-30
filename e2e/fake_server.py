"""模拟一个 Vanilla Minecraft 服务端，用于 MCDR 端到端测试。

行为：
* 打印与真实服务端一致的启动横幅（含 ``Done (0.512s)! For help, type "help"``），
  让 MCDR 的 vanilla_handler 正确识别「服务器已启动」
* 从 stdin 读取 MCDR 下发的指令，把**原始指令**逐行追加到 ``commands.log``
* 收到 ``stop`` 后打印关服日志并退出（MCDR 会据此认为服务器已停止）
* 每次启动都往 ``starts.log`` 追加一行，用于验证「重启」真的发生了
"""

import datetime
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COMMAND_LOG = os.path.join(HERE, 'commands.log')
START_LOG = os.path.join(HERE, 'starts.log')

WORLD_READY_DELAY = 0.3


def _append(path: str, text: str) -> None:
    with open(path, 'a', encoding='utf-8') as file_handler:
        file_handler.write(text + '\n')


def _emit(text: str) -> None:
    sys.stdout.write(text + '\n')
    sys.stdout.flush()


def _timestamp() -> str:
    return datetime.datetime.now().strftime('%H:%M:%S')


def _log_line(thread: str, content: str) -> None:
    _emit(f'[{_timestamp()}] [{thread}/INFO]: {content}')


def main() -> None:
    sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    sys.stdout.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)

    _append(START_LOG, f'{datetime.datetime.now().isoformat(timespec="seconds")} 服务端启动 pid={os.getpid()}')

    _log_line('Server thread', 'Starting minecraft server version 1.20.4')
    _log_line('Server thread', 'Preparing level "world"')
    time.sleep(WORLD_READY_DELAY)
    _log_line('Server thread', 'Done (0.512s)! For help, type "help"')

    stop_requested = False
    for raw_line in sys.stdin:
        command = raw_line.rstrip('\r\n')
        if command.strip() == '':
            continue
        _append(COMMAND_LOG, f'{datetime.datetime.now().isoformat(timespec="seconds")} | {command}')
        if command.strip().lower() == 'stop':
            stop_requested = True
            break
        # 只回一句笼统的确认，避免把指令原文回显进服务器输出干扰 MCDR 的解析
        _log_line('Server thread', '[Server] command executed')
        _log_line('Server thread', f'[Server] executed {len(command)} chars command')

    if stop_requested:
        _log_line('Server thread', 'Stopping the server')
    _log_line('Server thread', 'Saving worlds')
    _log_line('Server thread', 'ThreadedAnvilChunkStorage: All dimensions are saved')
    sys.exit(0)


if __name__ == '__main__':
    main()
