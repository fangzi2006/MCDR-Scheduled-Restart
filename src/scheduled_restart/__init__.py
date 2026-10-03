"""MCDR 定时重启插件（入口模块）

功能：

* 用 Linux cron 表达式（5 段式 ``分 时 日 月 周``，也支持 6 段式带秒）描述月/周/日等任意重启时间
* 每条计划可以挂多条提醒（数组里放对象），每条提醒可自由设置提前时间与通知方式
  （聊天框 / 大标题 / 物品栏 / 执行指令）
* 玩家进服时私聊告知当前重启倒计时，重启历史落盘可查

指令见 :mod:`scheduled_restart.command`，配置说明见 ``config/scheduled_restart/config.json``。
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from mcdreforged.api.types import Info, PluginServerInterface

from .command import register_commands
from .config import Config, load_config
from .config_store import ConfigFileEditor
from .scheduler import RestartScheduler

PLUGIN_ID = 'scheduled_restart'
PLUGIN_VERSION = '1.2.1'

# 注意：这里的全局名不要用 config / scheduler，
# 否则会遮蔽同名的子模块属性（from scheduled_restart import config 会拿到 None）
_config: Optional[Config] = None
_scheduler: Optional[RestartScheduler] = None


def get_config() -> Optional[Config]:
    """当前生效的配置"""
    return _config


def get_scheduler() -> Optional[RestartScheduler]:
    """当前的调度器"""
    return _scheduler


def _logger(server: PluginServerInterface) -> logging.Logger:
    return server.logger


def _log_config_issues(server: PluginServerInterface, loaded: Config) -> None:
    logger = _logger(server)
    for warning in loaded.warnings:
        logger.warning(f'[{PLUGIN_ID}] 配置警告: {warning}')
    for error in loaded.errors:
        logger.error(f'[{PLUGIN_ID}] 配置错误: {error}')
    if loaded.errors:
        logger.error(
            f'[{PLUGIN_ID}] 上面这些计划已被跳过，且不会回写配置文件；'
            f'请修正 config/{PLUGIN_ID}/config.json 后执行 !!srestart reload'
        )


def _describe_schedules(loaded: Config) -> str:
    enabled = [schedule for schedule in loaded.schedules if schedule.enabled]
    if not loaded.schedules:
        return '没有配置任何重启计划'
    if not enabled:
        return f'共 {len(loaded.schedules)} 个计划，但全部处于禁用状态'
    return f'共 {len(loaded.schedules)} 个计划，其中 {len(enabled)} 个已启用：' + '、'.join(
        f'{schedule.name}（{schedule.cron.describe()}）' for schedule in enabled
    )


def _apply_config(server: PluginServerInterface) -> Tuple[bool, str]:
    """重新读取配置文件并让调度生效

    ``!!srestart reload`` 以及会改配置文件的 ``enable / disable / add / remove``
    都走这里；返回 (是否成功, 给用户看的说明)。
    """
    global _config
    try:
        new_config = load_config(server)
    except Exception as e:
        server.logger.exception(f'[{PLUGIN_ID}] 重新加载配置失败')
        return False, f'重新加载配置失败：{e}'

    _config = new_config
    _log_config_issues(server, _config)
    if _scheduler is not None:
        _scheduler.reload(_config)
    return True, (
        f'配置已生效：{_describe_schedules(_config)}；'
        f'{len(_config.warnings)} 条警告，{len(_config.errors)} 条错误'
    )


# --------------------------------------------------------------------------- #
#                                 MCDR 生命周期                                #
# --------------------------------------------------------------------------- #


def on_load(server: PluginServerInterface, prev_module) -> None:
    global _config, _scheduler

    _config = load_config(server)
    _log_config_issues(server, _config)

    _scheduler = RestartScheduler(server, _config)
    register_commands(
        server,
        _scheduler,
        config_provider=get_config,
        apply_config=lambda: _apply_config(server),
        editor=ConfigFileEditor(server),
        command_alias=_config.command_alias,
    )
    _scheduler.start()

    alias_hint = f'，简化指令 {_config.command_alias} 已启用' if _config.command_alias else ''
    server.logger.info(f'[{PLUGIN_ID}] v{PLUGIN_VERSION} 已加载：{_describe_schedules(_config)}{alias_hint}')
    server.logger.info(f'[{PLUGIN_ID}] 使用 !!srestart help 查看指令，配置文件：config/{PLUGIN_ID}/config.json')
    if prev_module is not None:
        server.logger.info(f'[{PLUGIN_ID}] 检测到插件重载，调度已按新配置重新开始')
    elif not _config.enabled:
        server.logger.warning(f'[{PLUGIN_ID}] 配置中的 enabled 为 false，定时重启当前处于关闭状态')


def on_unload(server: PluginServerInterface) -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None
    server.logger.info(f'[{PLUGIN_ID}] 已卸载，调度线程已停止')


def on_player_joined(server: PluginServerInterface, player: str, info: Info) -> None:
    if _scheduler is not None:
        _scheduler.on_player_joined(player)
