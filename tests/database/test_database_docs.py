from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_documents_every_database_cli_operation_with_neutral_paths():
    expected_commands = {
        "python -m infrastructure.database.cli --database runtime/state.sqlite3 --json status",
        "python -m infrastructure.database.cli --database runtime/state.sqlite3 --json check",
        "python -m infrastructure.database.cli --database runtime/state.sqlite3 --json migrate --backup-dir runtime/backups",
        "python -m infrastructure.database.cli --database runtime/state.sqlite3 --json backup --output runtime/backups/state.sqlite3",
        "python -m infrastructure.database.cli --database runtime/state.sqlite3 --json verify-restore --backup runtime/backups/state.sqlite3 --manifest runtime/backups/state.sqlite3.manifest.json --target runtime/restore-candidates/state.sqlite3",
    }

    assert expected_commands.issubset(set(README.splitlines()))


def test_readme_documents_cli_output_contract_and_fixed_exit_codes():
    assert "固定退出码" in README
    assert "结构化 JSON" in README
    for field in ("`schema_version`", "`command`", "`status`", "`error_code`", "`details`"):
        assert field in README
    for code in ("`0`", "`2`", "`3`", "`4`", "`5`"):
        assert code in README


def test_readme_documents_migration_backup_and_restore_safety():
    required_text = (
        "迁移前必须先创建并验证一致性备份",
        "备份清单记录 SHA-256 和字节大小",
        "备份和恢复都不覆盖已存在的目标路径",
        "恢复只创建候选数据库",
        "由运维人员显式切换",
    )

    assert all(text in README for text in required_text)


def test_readme_documents_restore_verification_lifecycle_order():
    expected = (
        "恢复顺序为：先验证 manifest、SHA-256 和字节大小；再用 SQLite Backup API "
        "物化临时候选库；随后在临时库执行 integrity、外键和 schema 验证；全部通过后"
        "才以 no-overwrite 方式发布最终候选路径"
    )
    assert expected in README


def test_readme_documents_node_local_trust_and_provider_boundaries():
    required_text = (
        "显式 `--database` 路径优先",
        "Windows 或 macOS 单机节点",
        "备份目录和恢复目标父目录必须是当前节点本机受信任目录",
        "异地执行节点只能操作该节点自己的本地 SQLite",
        "PostgreSQL 是后续 provider",
        "central 模式不会回退到 SQLite",
    )

    assert all(text in README for text in required_text)


def test_readme_keeps_storage_and_parsing_adapters_out_of_database_dependency():
    assert "数据库实现不依赖固定的 SynologyDrive、PageIndex、OCR" in README
    assert "归档物理路径" in README
    assert "当前阶段尚未实现 SQLite Repository、迁移或 Unit of Work" not in README


def test_main_remains_independent_of_database_kernel():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "infrastructure.database" not in source
