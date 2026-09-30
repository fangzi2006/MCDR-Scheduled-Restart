import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'src')

for path in (SRC, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture
def root_dir() -> str:
    return ROOT


@pytest.fixture
def src_dir() -> str:
    return SRC


def pytest_sessionfinish(session, exitstatus) -> None:
    """测试结束后清掉工程里的临时目录"""
    from fakes import TEST_TMP_ROOT
    shutil.rmtree(TEST_TMP_ROOT, ignore_errors=True)
