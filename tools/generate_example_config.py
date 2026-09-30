"""根据代码里的默认配置生成 ``examples/config.json``

这样示例配置与实际生成的配置永远一致（有测试保证不会漂移）::

    python tools/generate_example_config.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from scheduled_restart.config import DEFAULT_CONFIG, Config  # noqa: E402


def main() -> None:
    config = Config.from_raw(copy.deepcopy(DEFAULT_CONFIG))
    assert config.errors == [] and config.warnings == [], (config.errors, config.warnings)

    output = ROOT / 'examples' / 'config.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(config.to_raw(), ensure_ascii=False, indent=4) + '\n',
        encoding='utf8',
    )
    print(f'已生成示例配置: {output}')


if __name__ == '__main__':
    main()
