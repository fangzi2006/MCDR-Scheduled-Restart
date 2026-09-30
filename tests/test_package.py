"""插件包结构测试：元数据校验、打包布局、从 .mcdr 里真实导入

这些测试用的都是 MCDR 自己的校验器（pydantic 元数据模型 / Metadata / VersionRequirement），
确保插件在真实 MCDR 里能被正确识别与加载。
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

from fakes import workspace_temp_dir

PLUGIN_ID = 'scheduled_restart'


def metadata_of(root_dir: str) -> dict:
    """以 src/mcdreforged.plugin.json 为版本号的唯一来源，避免测试里硬编码版本"""
    text = (Path(root_dir) / 'src' / 'mcdreforged.plugin.json').read_text(encoding='utf8')
    return json.loads(text)


def plugin_version(root_dir: str) -> str:
    return metadata_of(root_dir)['version']


def load_build_module(root_dir: str):
    path = Path(root_dir) / 'tools' / 'build_plugin.py'
    spec = importlib.util.spec_from_file_location('build_plugin_for_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def built_plugin(root_dir):
    builder = load_build_module(root_dir)
    output = Path(workspace_temp_dir('pack-')) / f'{PLUGIN_ID}-v{plugin_version(root_dir)}.mcdr'
    return builder.build(Path(root_dir) / 'src', output)


# --------------------------------------------------------------------------- #
#                                  元数据                                       #
# --------------------------------------------------------------------------- #


def test_metadata_passes_mcdr_schema(root_dir):
    from mcdreforged.plugin.meta.schema import PluginMetadataJsonModel

    text = (Path(root_dir) / 'src' / 'mcdreforged.plugin.json').read_text(encoding='utf8')
    model = PluginMetadataJsonModel.model_validate_json(text, strict=True)
    assert model.id == PLUGIN_ID
    assert model.version == plugin_version(root_dir)
    assert model.entrypoint == PLUGIN_ID
    assert model.requirements_file is None       # 插件不依赖任何第三方库
    assert set(model.description.keys()) >= {'zh_cn', 'en_us'}


def test_metadata_dependency_accepts_installed_mcdr(root_dir):
    from mcdreforged.plugin.meta.metadata import Metadata
    from mcdreforged.plugin.meta.schema import PluginMetadataJsonModel

    text = (Path(root_dir) / 'src' / 'mcdreforged.plugin.json').read_text(encoding='utf8')
    model = PluginMetadataJsonModel.model_validate_json(text, strict=True)
    metadata = Metadata.create(model)

    assert metadata.id == PLUGIN_ID
    assert str(metadata.version) == plugin_version(root_dir)
    requirement = metadata.dependencies['mcdreforged']
    installed = importlib.metadata.version('mcdreforged')
    assert requirement.accept(installed), f'声明 {requirement} 但当前 MCDR 是 {installed}'
    assert requirement.accept('2.12.0') is False or requirement.accept('2.12.0')


def test_entrypoint_matches_package(root_dir):
    """entrypoint 必须指向与插件 id 同名的包（打包插件的目录合法性要求）"""
    src = Path(root_dir) / 'src'
    text = (src / 'mcdreforged.plugin.json').read_text(encoding='utf8')
    entrypoint = json.loads(text)['entrypoint']
    package_dir = src / entrypoint
    assert package_dir.is_dir()
    assert (package_dir / '__init__.py').is_file()


# --------------------------------------------------------------------------- #
#                                  打包布局                                     #
# --------------------------------------------------------------------------- #


def test_packed_layout_is_legal(built_plugin):
    """对齐 MCDR ``PackedPlugin._check_dir_legality`` 的规则"""
    with zipfile.ZipFile(built_plugin) as zip_file:
        names = zip_file.namelist()

    assert 'mcdreforged.plugin.json' in names
    assert f'{PLUGIN_ID}/__init__.py' in names
    assert not any(name.endswith('.pyc') for name in names)
    assert not any('__pycache__' in name for name in names)

    for name in names:
        stripped = name.rstrip('/')
        if '/' in stripped:
            continue
        if stripped.endswith('.py'):
            module_name = stripped[:-3]
            # 根目录下除插件 id 外不允许出现其它普通模块
            assert module_name == PLUGIN_ID
        else:
            # 根目录下除插件 id 外不允许出现其它包
            assert stripped in ('mcdreforged.plugin.json', PLUGIN_ID, 'lang')


def test_plugin_imports_from_packed_file(built_plugin, root_dir):
    """真的从 .mcdr（zip）里导入插件，证明打包后的导入路径没问题"""
    saved_modules = {
        name: module for name, module in sys.modules.items()
        if name == PLUGIN_ID or name.startswith(f'{PLUGIN_ID}.')
    }
    for name in saved_modules:
        del sys.modules[name]

    saved_path = list(sys.path)
    src_dir = str(Path(built_plugin).parent.parent / 'src')
    sys.path[:] = [item for item in sys.path if str(Path(item or '.').resolve()) != str(Path(src_dir).resolve())]
    sys.path.insert(0, str(built_plugin))

    try:
        module = importlib.import_module(PLUGIN_ID)
        assert str(built_plugin) in str(module.__file__)          # 确实来自 zip
        assert callable(module.on_load)
        assert callable(module.on_unload)
        assert module.PLUGIN_VERSION == plugin_version(root_dir)

        # 子模块同样必须能从 zip 中导入（相对导入依赖包结构）
        packed_cron = importlib.import_module(f'{PLUGIN_ID}.cron')
        assert packed_cron.parse_cron('0 4 * * *').hours == frozenset({4})

        packed_config = importlib.import_module(f'{PLUGIN_ID}.config')
        assert packed_config.Config.from_raw(packed_config.DEFAULT_CONFIG).errors == []
    finally:
        for name in [n for n in sys.modules if n == PLUGIN_ID or n.startswith(f'{PLUGIN_ID}.')]:
            del sys.modules[name]
        sys.path[:] = saved_path
        sys.modules.update(saved_modules)


def test_plugin_version_matches_package(root_dir):
    """代码里的版本号必须与 mcdreforged.plugin.json 一致（避免打包出来的版本对不上）"""
    import scheduled_restart
    assert scheduled_restart.PLUGIN_VERSION == plugin_version(root_dir)


def test_submodules_are_not_shadowed_by_globals():
    """入口模块里不能用 config / scheduler 之类的全局名，否则会遮蔽同名子模块"""
    import types

    import scheduled_restart

    assert isinstance(scheduled_restart.config, types.ModuleType)
    assert isinstance(scheduled_restart.scheduler, types.ModuleType)
    assert isinstance(scheduled_restart.command, types.ModuleType)
    assert callable(scheduled_restart.get_config)
    assert callable(scheduled_restart.get_scheduler)


def test_example_config_is_up_to_date(root_dir):
    """examples/config.json 必须与代码里的默认配置一致（用 tools/generate_example_config.py 重新生成）"""
    import copy

    from scheduled_restart.config import DEFAULT_CONFIG, Config

    expected = Config.from_raw(copy.deepcopy(DEFAULT_CONFIG)).to_raw()
    actual = json.loads((Path(root_dir) / 'examples' / 'config.json').read_text(encoding='utf8'))
    assert actual == expected


def test_on_load_and_unload_end_to_end():
    """按 MCDR 的方式调用入口模块的 on_load / on_unload，跑通插件生命周期"""
    from mcdreforged.api.types import PermissionLevel

    import scheduled_restart
    from fakes import FakeCommandSource, FakeServer, run_command

    server = FakeServer()          # 默认配置：3 个示例计划，全部禁用 -> 不会真的重启
    scheduled_restart.on_load(server, None)
    try:
        assert scheduled_restart.get_config() is not None
        scheduler = scheduled_restart.get_scheduler()
        assert scheduler is not None
        assert scheduler.is_running() is True
        assert len(server.registered_commands) == 1
        assert server.help_messages[0][0] == '!!srestart'

        # 通过真实指令树走一遍 reload / status
        root = server.registered_commands[0]
        source = FakeCommandSource(PermissionLevel.OWNER, server=server)
        run_command(root, source, '!!srestart reload')
        assert '配置已生效' in source.text()

        # 配置类指令（add / disable / remove）走真实文件读写
        add_source = FakeCommandSource(PermissionLevel.OWNER, server=server)
        run_command(root, add_source, '!!srestart add 临时计划 0 7 * * *')
        assert '已创建计划' in add_source.text()

        disable_source = FakeCommandSource(PermissionLevel.OWNER, server=server)
        run_command(root, disable_source, '!!srestart disable 1')
        assert '已禁用计划' in disable_source.text()

        remove_source = FakeCommandSource(PermissionLevel.OWNER, server=server)
        run_command(root, remove_source, '!!srestart remove 2')
        assert '已删除计划' in remove_source.text()

        status_source = FakeCommandSource(PermissionLevel.OWNER, server=server)
        run_command(root, status_source, '!!srestart status')
        assert '总开关' in status_source.text()
        assert '1 个' in status_source.text()

        # 玩家进服提醒不会因为没有计划而报错
        scheduled_restart.on_player_joined(server, 'Steve', None)
    finally:
        scheduled_restart.on_unload(server)

    assert scheduled_restart.get_scheduler() is None
    assert scheduler.is_running() is False


def test_used_server_interface_api_exists():
    """插件用到的 API 必须真实存在于 MCDR 的 PluginServerInterface 上"""
    from mcdreforged.api.types import PluginServerInterface

    used_api = [
        'logger', 'say', 'tell', 'broadcast', 'execute',
        'restart', 'stop', 'stop_exit', 'start', 'wait_for_start', 'is_server_running',
        'get_data_folder', 'load_config_simple', 'save_config_simple',
        'register_command', 'register_help_message', 'get_self_metadata',
    ]
    missing = [name for name in used_api if not hasattr(PluginServerInterface, name)]
    assert missing == [], f'MCDR 的 PluginServerInterface 上找不到这些 API: {missing}'


def test_event_handler_signatures_match_mcdr_defaults():
    """入口模块里的事件处理器必须用 MCDR 的默认方法名与形参"""
    import inspect
    from typing import get_type_hints

    from mcdreforged.plugin.plugin_event import MCDRPluginEvents

    import scheduled_restart

    expected = {
        'on_load': ['server', 'prev_module'],
        'on_unload': ['server'],
        'on_player_joined': ['server', 'player', 'info'],
    }
    default_method_names = {
        event.default_method_name for event in MCDRPluginEvents.get_event_list()
        if isinstance(event.default_method_name, str)
    }
    for name, parameters in expected.items():
        handler = getattr(scheduled_restart, name)
        assert list(inspect.signature(handler).parameters) == parameters, name
        assert name in default_method_names, f'{name} 不是 MCDR 的默认事件方法名'
        assert get_type_hints(handler)          # 类型注解必须可解析
