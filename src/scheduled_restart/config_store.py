"""直接读写配置文件（``config/scheduled_restart/config.json``）。

``!!srestart enable / disable / add / remove`` 走的就是这里：
只改动 ``schedules`` 数组里对应条目的字段，其余内容（用户自己写的其它字段、
其它计划的自定义字段、``_readme`` 说明等）原样保留，写回后再让插件热重载配置。

写文件采用「先写临时文件再替换」的方式，避免写一半把用户的配置弄坏。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .config import config_file_path, default_schedule_entry, preprocess_config_file
from .cron import CronError, parse_cron

__all__ = ['ConfigFileEditor']


class ConfigFileEditor:
    """配置文件的读改写"""

    def __init__(self, server):
        self._server = server

    # ------------------------------------------------------------------ 路径

    @property
    def data_folder(self) -> str:
        return self._server.get_data_folder()

    @property
    def file_path(self) -> str:
        return config_file_path(self._server)

    # ------------------------------------------------------------------ 读写

    def read_raw(self) -> Dict[str, Any]:
        """读取配置文件；文件不存在时返回内置默认配置

        读取前会先去掉可能存在的 UTF-8 BOM（Windows 记事本等会写 BOM）。
        """
        from .config import DEFAULT_CONFIG
        preprocess_config_file(self._server)
        path = self.file_path
        if not os.path.isfile(path):
            return json.loads(json.dumps(DEFAULT_CONFIG, ensure_ascii=False))
        with open(path, encoding='utf-8-sig') as file_handler:
            data = json.load(file_handler)
        if not isinstance(data, dict):
            raise ValueError(f'配置文件 {path} 的根节点必须是对象')
        return data

    def write_raw(self, raw: Dict[str, Any]) -> None:
        """原子写入配置文件"""
        path = self.file_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = path + '.tmp'
        with open(temp_path, 'w', encoding='utf8') as file_handler:
            json.dump(raw, file_handler, ensure_ascii=False, indent=4)
            file_handler.write('\n')
        os.replace(temp_path, path)

    # ------------------------------------------------------------- schedules

    @staticmethod
    def schedules_of(raw: Dict[str, Any]) -> List[Any]:
        schedules = raw.get('schedules')
        if not isinstance(schedules, list):
            raise ValueError('配置文件里的 schedules 必须是一个数组')
        return schedules

    def entry_name(self, file_index: int) -> str:
        """取某个文件下标对应计划的名字（用于回复文案）"""
        try:
            entry = self.schedules_of(self.read_raw())[file_index]
        except (ValueError, IndexError, KeyError):
            return f'#{file_index + 1}'
        if isinstance(entry, dict) and isinstance(entry.get('name'), str) and entry['name'].strip():
            return entry['name']
        return f'#{file_index + 1}'

    # ------------------------------------------------------------------ 修改

    def set_enabled(self, file_index: int, enabled: bool) -> Tuple[bool, str]:
        raw = self.read_raw()
        schedules = self.schedules_of(raw)
        if not (0 <= file_index < len(schedules)):
            return False, f'配置文件里没有第 {file_index + 1} 项计划'
        entry = schedules[file_index]
        if not isinstance(entry, dict):
            return False, f'配置文件里第 {file_index + 1} 项不是计划对象，无法开关'

        entry['enabled'] = enabled
        self.write_raw(raw)

        name = entry.get('name') if isinstance(entry.get('name'), str) else f'#{file_index + 1}'
        action = '已启用' if enabled else '已禁用'
        message = f'{action}计划 #{file_index + 1} {name}（已写入配置文件，立即生效）'

        # 开关一个 cron 写坏了的计划没有意义，这里提醒一下
        try:
            parse_cron(str(entry.get('cron', '')))
        except CronError as e:
            message += f'；但它当前的 cron 有误，修正后才会真正生效：{e}'
        return True, message

    def set_all_enabled(self, enabled: bool) -> Tuple[bool, str]:
        raw = self.read_raw()
        schedules = self.schedules_of(raw)
        changed = 0
        for entry in schedules:
            if isinstance(entry, dict):
                entry['enabled'] = enabled
                changed += 1
        if changed == 0:
            return False, '配置文件里没有任何计划'
        self.write_raw(raw)
        action = '已启用' if enabled else '已禁用'
        return True, f'{action}全部 {changed} 个计划（已写入配置文件，立即生效）'

    def add_schedule(self, name: str, cron_expression: str) -> Tuple[bool, str]:
        name = str(name).strip()
        cron_expression = ' '.join(str(cron_expression).split())
        if name == '':
            return False, '计划名不能为空'
        if name.isdigit():
            return False, '计划名不能是纯数字（会和序号混淆），请换一个名字'

        try:
            cron = parse_cron(cron_expression)
        except CronError as e:
            return False, f'cron 表达式有误：{e}'

        raw = self.read_raw()
        schedules = self.schedules_of(raw)
        for entry in schedules:
            if isinstance(entry, dict) and isinstance(entry.get('name'), str) and entry['name'] == name:
                return False, f'已经存在名为 {name!r} 的计划，请换一个名字'

        schedules.append(default_schedule_entry(name=name, cron=cron_expression, enabled=True))
        self.write_raw(raw)
        return True, (
            f'已创建计划 #{len(schedules)} {name}（{cron.describe()}，cron: {cron.expression}）；'
            f'默认已启用，并使用顶层 default_notifications 提醒组'
        )

    def remove_schedule(self, file_index: int) -> Tuple[bool, str]:
        raw = self.read_raw()
        schedules = self.schedules_of(raw)
        if not (0 <= file_index < len(schedules)):
            return False, f'配置文件里没有第 {file_index + 1} 项计划'
        removed = schedules.pop(file_index)
        self.write_raw(raw)
        if isinstance(removed, dict):
            name = removed.get('name', f'#{file_index + 1}')
            cron = removed.get('cron', '?')
        else:
            name, cron = f'#{file_index + 1}', '?'
        return True, f'已删除计划 {name}（原 cron: {cron}）；如需恢复请重新 add 或手动编辑配置文件'


def schedule_name_at(file_index: int, schedules: List[Any]) -> Optional[str]:
    """（供测试/调试使用的）取名字小工具"""
    if 0 <= file_index < len(schedules):
        entry = schedules[file_index]
        if isinstance(entry, dict):
            return entry.get('name')
    return None
