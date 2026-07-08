"""pytest fixtures for adversarial_verification tests.

提供：
- tmp_workspace：临时工作目录（隔离 runs/ 与 adversarial_verification/ 副作用）
- baseline_snapshot：从 tests/adversarial_verification/fixtures/snapshot_v1.json 加载
"""
import json
import os
import shutil
import tempfile

import pytest


@pytest.fixture
def tmp_workspace():
    """临时工作目录 fixture，自动清理。"""
    path = tempfile.mkdtemp(prefix="av_test_")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def baseline_snapshot():
    """加载 frozen snapshot（PRD §7.5.5 稳定性要求：10 次重放稳定率 ≥ 95%）。"""
    fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
    snapshot_path = os.path.join(fixtures_dir, "snapshot_v1.json")
    with open(snapshot_path, "r", encoding="utf-8") as f:
        return json.load(f)
