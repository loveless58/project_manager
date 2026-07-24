import json
import os
from pathlib import Path
import re
import subprocess


PROJECT_DIR = Path(__file__).resolve().parents[1]


def _git_ls_files(*pathspecs: str) -> list[str]:
    environment = os.environ.copy()
    marker = PROJECT_DIR / ".git"
    if marker.is_file():
        raw_git_dir = marker.read_text(encoding="utf-8").split(":", 1)[1].strip()
        wsl_path = re.fullmatch(r"/mnt/([A-Za-z])/(.*)", raw_git_dir)
        if os.name == "nt" and wsl_path:
            raw_git_dir = f"{wsl_path.group(1).upper()}:/{wsl_path.group(2)}"
        environment["GIT_DIR"] = raw_git_dir
        environment["GIT_WORK_TREE"] = str(PROJECT_DIR)
    return subprocess.run(
        ["git", "ls-files", *pathspecs],
        cwd=PROJECT_DIR,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()


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


def test_runtime_trace_json_is_ignored_and_not_tracked():
    ignore_text = (PROJECT_DIR / ".gitignore").read_text(encoding="utf-8")

    assert _git_ls_files("logs") == []
    assert "logs/*.json" in ignore_text.splitlines()


def test_node_local_database_artifacts_have_narrow_ignore_rules():
    ignore_lines = (PROJECT_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()

    for rule in (
        "/.project_manager/",
        "/state/project_manager.sqlite3",
        "/state/project_manager.sqlite3-wal",
        "/state/project_manager.sqlite3-shm",
        "/state/backups/",
        "/runtime/state/*.sqlite3*",
        "/runtime/backups/",
        "/runtime/restore-candidates/",
    ):
        assert rule in ignore_lines
    assert "*.db" not in ignore_lines
    assert "*.sqlite3" not in ignore_lines


def test_readme_repository_hygiene_matches_runtime_trace_policy():
    readme = (PROJECT_DIR / "README.md").read_text(encoding="utf-8")

    assert "仓库不提交客户业务原件、运行日志" in readme
