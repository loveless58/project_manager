import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_official_examples_keep_runtime_and_sqlite_node_local():
    readme = (PROJECT_DIR / "README.md").read_text(encoding="utf-8")
    example = json.loads(
        (PROJECT_DIR / "config" / "project-manager.example.json").read_text(
            encoding="utf-8"
        )
    )

    assert 'Join-Path $env:LOCALAPPDATA "ProjectManager"' in readme
    assert '${XDG_STATE_HOME:-$HOME/.local/state}/project_manager' in readme
    assert "映射网络盘" in readme
    assert "SQLite" in readme and "同步" in readme
    assert example["runtime_workspace"] == "~/.project_manager"
    assert example["database"]["sqlite_path"].startswith("~/.project_manager/")
    assert "ProjectManagerData" not in example["runtime_workspace"]
    assert "ProjectManagerData" not in example["database"]["sqlite_path"]
