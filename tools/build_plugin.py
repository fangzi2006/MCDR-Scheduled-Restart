"""把 ``src/`` 打包成 MCDR 插件包（``.mcdr`` 本质是一个 zip）

用法::

    python tools/build_plugin.py                  # 输出到 dist/<插件 id>-v<版本>.mcdr
    python tools/build_plugin.py -o out/x.mcdr    # 指定输出路径
    python tools/build_plugin.py -s 别的/src -o out.mcdr

包内结构（MCDR 打包插件的要求：根目录只能有 ``mcdreforged.plugin.json``
以及与插件 id 同名的那个包）::

    mcdreforged.plugin.json
    scheduled_restart/__init__.py
    scheduled_restart/...

也可以直接作为目录插件使用：把 ``src/`` **里面**的内容整体放进 MCDR 的
``plugins/scheduled_restart/``（注意不是把 ``src`` 目录本身放进去）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'src'
DIST = ROOT / 'dist'

#: 打包时跳过的目录名
EXCLUDE_DIRS = {'__pycache__', '.mypy_cache', '.pytest_cache'}


def read_metadata(src: Path = SRC) -> dict:
    with open(Path(src) / 'mcdreforged.plugin.json', encoding='utf8') as file_handler:
        return json.load(file_handler)


def default_output(src: Path = SRC, dist: Path = DIST) -> Path:
    metadata = read_metadata(src)
    return Path(dist) / f"{metadata['id']}-v{metadata['version']}.mcdr"


def calc_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as file_handler:
        for chunk in iter(lambda: file_handler.read(1 << 16), b''):
            digest.update(chunk)
    return digest.hexdigest()


def build(src: Path = SRC, out: Path = None) -> Path:
    """打包并返回生成的 ``.mcdr`` 路径"""
    src = Path(src)
    out = Path(out) if out is not None else default_output(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for path in sorted(src.rglob('*')):
            if not path.is_file():
                continue
            if path.resolve() == out.resolve():
                continue        # 输出文件恰好落在源码目录里时不要把它自己打进去
            relative = path.relative_to(src)
            if any(part in EXCLUDE_DIRS for part in relative.parts):
                continue
            if path.suffix in ('.pyc', '.pyo'):
                continue
            zip_file.write(path, relative.as_posix())
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description='打包 scheduled_restart 插件')
    parser.add_argument('-s', '--src', default=str(SRC), help='源码目录（含 mcdreforged.plugin.json）')
    parser.add_argument('-o', '--output', default=None, help='输出的 .mcdr 路径')
    args = parser.parse_args()

    output = build(args.src, args.output)
    size = output.stat().st_size
    print(f'已生成插件包: {output}（{size} 字节）')
    print(f'sha256: {calc_file_sha256(output)}')


if __name__ == '__main__':
    main()
