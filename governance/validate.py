#!/usr/bin/env python3
"""Project Manager Agent 的目录、工具和 loop package 治理校验。"""

import json
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from platform_core.settings import AppSettings, load_app_settings  # noqa: E402


def load_contract(filename: str) -> dict:
    """加载治理契约 JSON。"""
    path = Path(__file__).resolve().parent / filename
    return json.loads(path.read_text(encoding="utf-8"))


def load_governance_settings() -> AppSettings:
    """Resolve settings for this node without inventing a machine-specific path."""
    return load_app_settings(
        runtime_workspace=os.environ.get("LOOP_PROJECT_BASE_DIR") or None
    )


def _marker_for(level: str) -> str:
    return {"error": "❌", "warning": "⚠️ ", "info": "ℹ️ "}.get(level, "⚠️ ")


def _record(
    findings: List[dict],
    finding: dict,
    verbose: bool,
) -> Tuple[int, int]:
    findings.append(finding)
    level = finding.get("level", "warning")
    if verbose:
        print(f"{_marker_for(level)} {finding['msg']}")
        if finding.get("fix"):
            print(f"   修复: {finding['fix']}")
    return (1 if level == "error" else 0, 1 if level == "warning" else 0)


def _directory_path(spec: dict, settings: AppSettings) -> Optional[Path]:
    """Map a contract path to the current node without hard-coded mount paths."""
    relative = Path(spec["path"])
    scope = spec.get("scope")
    if scope == "project":
        return PROJECT_ROOT / relative
    if scope == "business":
        return settings.business_root / relative if settings.business_root else None
    return settings.runtime_workspace / relative


def validate_dirs(verbose: bool = True) -> Tuple[int, int, List[dict]]:
    """Validate configured runtime/business directories with informational findings."""
    contract = load_contract("directory_contract.json")
    settings = load_governance_settings()
    findings: List[dict] = []
    error_count = 0
    warning_count = 0

    for key, spec in contract.get("required_dirs", {}).items():
        full_path = _directory_path(spec, settings)
        level = spec.get("validation_level", "warning")
        if full_path is None:
            errors, warnings = _record(
                findings,
                {
                    "id": f"DIR-{key}-CONFIG",
                    "level": level,
                    "msg": f"[{level.upper()}] {key}: PROJECT_MANAGER_BUSINESS_ROOT 未配置",
                    "fix": "为当前执行节点显式配置 PROJECT_MANAGER_BUSINESS_ROOT。",
                },
                verbose,
            )
            error_count += errors
            warning_count += warnings
            continue

        if not full_path.exists():
            errors, warnings = _record(
                findings,
                {
                    "id": f"DIR-{key}",
                    "level": level,
                    "msg": f"[{level.upper()}] {key}: {full_path} 不存在",
                    "fix": f"在当前节点配置或创建目录 {full_path}",
                },
                verbose,
            )
            error_count += errors
            warning_count += warnings
            continue

        if verbose:
            print(f"✅ {key}: {full_path} 存在")

        if spec.get("scope") == "project":
            continue
        for subdir in spec.get("required_subdirs", []):
            candidate = full_path / subdir
            if candidate.is_dir():
                continue
            errors, warnings = _record(
                findings,
                {
                    "id": f"DIR-{key}-{subdir}",
                    "level": level,
                    "msg": f"[{level.upper()}] {key}.{subdir}: 必需子目录不存在",
                    "fix": f"创建 {candidate}",
                },
                verbose,
            )
            error_count += errors
            warning_count += warnings

        for filename in spec.get("required_files", []):
            candidate = full_path / filename
            if candidate.is_file():
                continue
            errors, warnings = _record(
                findings,
                {
                    "id": f"DIR-{key}-{filename}",
                    "level": level,
                    "msg": f"[{level.upper()}] {key}.{filename}: 必需文件不存在",
                    "fix": f"运行相关脚本生成 {candidate}",
                },
                verbose,
            )
            error_count += errors
            warning_count += warnings

    return error_count, warning_count, findings


def validate_tools(verbose: bool = True) -> Tuple[int, int, List[dict]]:
    """Compare the registered debug tools with the checked-in schema contract."""
    contract = load_contract("project_schema.json")
    expected_tools = set(contract.get("tools", {}))
    findings: List[dict] = []
    try:
        import main as main_module  # noqa: E402

        actual_tools = set(main_module._build_registry().list_tools())
    except Exception as exc:
        msg = f"[ERROR] 无法导入 main._build_registry(): {exc}"
        findings.append({"id": "TOOL-IMPORT", "level": "error", "msg": msg})
        if verbose:
            print(f"❌ {msg}")
        return 1, 0, findings

    error_count = 0
    warning_count = 0
    if len(actual_tools) != len(expected_tools):
        errors, warnings = _record(
            findings,
            {
                "id": "TOOL-001",
                "level": "error",
                "msg": f"[ERROR] 工具数量不匹配: 实际 {len(actual_tools)} != 期望 {len(expected_tools)}",
            },
            verbose,
        )
        error_count += errors
        warning_count += warnings

    for tool in sorted(expected_tools - actual_tools):
        errors, warnings = _record(
            findings,
            {
                "id": f"TOOL-MISSING-{tool}",
                "level": "error",
                "msg": f"[ERROR] 工具 {tool} 在 schema 里但未注册",
                "fix": f"在 main._build_registry() 注册 {tool}",
            },
            verbose,
        )
        error_count += errors
        warning_count += warnings

    for tool in sorted(actual_tools - expected_tools):
        errors, warnings = _record(
            findings,
            {
                "id": f"TOOL-EXTRA-{tool}",
                "level": "warning",
                "msg": f"[WARNING] 工具 {tool} 已注册但不在 schema 里",
                "fix": f"在 governance/project_schema.json 添加 {tool}",
            },
            verbose,
        )
        error_count += errors
        warning_count += warnings

    if verbose and not error_count and not warning_count:
        print(f"✅ 工具 schema 一致: {len(actual_tools)} 个工具")
    return error_count, warning_count, findings


def validate_loop_packages(verbose: bool = True) -> Tuple[int, int, List[dict]]:
    """Validate loop-package manifests against runtime registry contracts."""
    findings: List[dict] = []
    try:
        from loop_packages import validate_all_loop_packages  # noqa: E402
    except Exception as exc:
        msg = f"[ERROR] cannot import loop package validator: {exc}"
        findings.append({"id": "LOOP-PACKAGE-IMPORT", "level": "error", "msg": msg})
        if verbose:
            print(f"❌ {msg}")
        return 1, 0, findings

    result = validate_all_loop_packages()
    for index, error in enumerate(result.get("errors", []), 1):
        msg = f"[ERROR] loop package validation failed: {error}"
        findings.append({"id": f"LOOP-PACKAGE-{index:03d}", "level": "error", "msg": msg})
        if verbose:
            print(f"❌ {msg}")
    if verbose and not findings:
        print(f"✅ loop package contracts consistent: {result.get('package_count', 0)} packages")
        print(f"   packages: {', '.join(result.get('packages', []))}")
    return len(findings), 0, findings


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python governance/validate.py {dirs|tools|loop-packages|all}")
        raise SystemExit(1)

    command = sys.argv[1]
    if command not in {"dirs", "tools", "loop-packages", "all"}:
        print(f"Unknown command: {command}")
        raise SystemExit(2)

    settings = load_governance_settings()
    print(f"📂 工作基址: {settings.runtime_workspace}")
    print(f"📦 项目根: {PROJECT_ROOT}")
    print()

    error_count = 0
    warning_count = 0
    checks = (
        ("dirs", "目录契约校验", validate_dirs),
        ("tools", "工具 schema 校验", validate_tools),
        ("loop-packages", "loop package 合约校验", validate_loop_packages),
    )
    for name, title, check in checks:
        if command not in (name, "all"):
            continue
        print("=" * 60)
        print(title)
        print("=" * 60)
        errors, warnings, _ = check()
        error_count += errors
        warning_count += warnings
        print()

    print("=" * 60)
    print(f"汇总: {error_count} errors, {warning_count} warnings")
    print("=" * 60)
    raise SystemExit(2 if error_count else 1 if warning_count else 0)


if __name__ == "__main__":
    main()
