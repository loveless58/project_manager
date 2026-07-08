#!/usr/bin/env python3
"""
Project Manager Agent - Governance Validator

校验项目目录契约与工具 schema 契约。

用法：
    # 校验目录契约
    python governance/validate.py dirs

    # 校验工具 schema
    python governance/validate.py tools

    # 全部校验
    python governance/validate.py all

退出码：
    0 - 全部通过
    1 - 有 warning
    2 - 有 error
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# 项目根目录（governance/ 的父目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PROJECT_ROOT))
from common.workspace_config import resolve_workspace_config  # noqa: E402

# 工作目录基址来自统一 workspace 配置；LOOP_PROJECT_BASE_DIR 仅保留为治理兼容入口。
WORKSPACE_CONFIG = resolve_workspace_config(
    runtime_workspace=os.environ.get("LOOP_PROJECT_BASE_DIR") or None
)
BASE_DIR = WORKSPACE_CONFIG.runtime_workspace


def load_contract(filename: str) -> dict:
    """加载 JSON 契约"""
    path = Path(__file__).resolve().parent / filename
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_dir(path: Path, base: Path) -> bool:
    """检查目录是否存在"""
    return (base / path).is_dir()


def check_file(path: Path, base: Path) -> bool:
    """检查文件是否存在"""
    return (base / path).is_file()


def validate_dirs(verbose: bool = True) -> Tuple[int, int, List[dict]]:
    """
    校验目录契约

    Returns:
        (error_count, warning_count, findings)
    """
    contract = load_contract("directory_contract.json")
    findings = []
    error_count = 0
    warning_count = 0

    def record_finding(finding: dict) -> None:
        nonlocal error_count, warning_count
        findings.append(finding)
        level = finding.get("level", "warning")
        if level == "error":
            error_count += 1
        elif level == "warning":
            warning_count += 1

    def marker_for(level: str) -> str:
        if level == "error":
            return "❌"
        if level == "warning":
            return "⚠️ "
        return "ℹ️ "

    for key, spec in contract.get("required_dirs", {}).items():
        rel_path = spec["path"]
        # scope=project 表示路径相对项目根目录；scope=business 表示路径相对业务根；
        # 其他默认相对 runtime workspace。
        if spec.get("scope") == "project":
            full_path = PROJECT_ROOT / rel_path
        elif spec.get("scope") == "business":
            full_path = WORKSPACE_CONFIG.business_root / rel_path
        else:
            full_path = BASE_DIR / rel_path

        if not full_path.exists():
            level = spec.get("validation_level", "warning")
            msg = f"[{level.upper()}] {key}: {full_path} 不存在"
            finding = {"id": f"DIR-{key}", "level": level, "msg": msg,
                       "fix": f"创建目录 {full_path}"}
            record_finding(finding)
            if verbose:
                print(f"{marker_for(level)} {msg}")
                print(f"   修复: {finding['fix']}")
            continue

        if verbose:
            print(f"✅ {key}: {full_path} 存在")

        # 检查必需子目录（项目根 scope 不检查业务运行子目录）
        if spec.get("scope") != "project":
            for subdir in spec.get("required_subdirs", []):
                if not (full_path / subdir).is_dir():
                    level = spec.get("validation_level", "warning")
                    msg = f"[{level.upper()}] {key}.{subdir}: 必需子目录不存在"
                    finding = {"id": f"DIR-{key}-{subdir}", "level": level, "msg": msg,
                               "fix": f"创建 {full_path / subdir}"}
                    record_finding(finding)
                    if verbose:
                        print(f"{marker_for(level)} {msg}")
                        print(f"   修复: {finding['fix']}")

            # 检查必需文件
            for filename in spec.get("required_files", []):
                if not (full_path / filename).is_file():
                    level = spec.get("validation_level", "warning")
                    msg = f"[{level.upper()}] {key}.{filename}: 必需文件不存在"
                    finding = {"id": f"DIR-{key}-{filename}", "level": level, "msg": msg,
                               "fix": f"运行相关脚本生成 {full_path / filename}"}
                    record_finding(finding)
                    if verbose:
                        print(f"{marker_for(level)} {msg}")
                        print(f"   修复: {finding['fix']}")

    return error_count, warning_count, findings


def validate_tools(verbose: bool = True) -> Tuple[int, int, List[dict]]:
    """
    校验工具 schema 契约

    通过动态 import main._build_registry() 获取实际注册的工具，跟 schema 对比。
    """
    contract = load_contract("project_schema.json")
    expected = contract.get("tools", {})
    findings = []
    error_count = 0
    warning_count = 0

    # 动态 import main 以获取 ToolRegistry
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        import main as main_module  # noqa: E402
        reg = main_module._build_registry()
        actual_tools = set(reg.list_tools())
    except Exception as e:
        msg = f"[ERROR] 无法导入 main._build_registry(): {e}"
        findings.append({"id": "TOOL-IMPORT", "level": "error", "msg": msg})
        if verbose:
            print(f"❌ {msg}")
        return 1, 0, findings

    expected_tools = set(expected.keys())

    # 1. 检查工具数量
    if len(actual_tools) != len(expected_tools):
        msg = f"[ERROR] 工具数量不匹配: 实际 {len(actual_tools)} != 期望 {len(expected_tools)}"
        findings.append({"id": "TOOL-001", "level": "error", "msg": msg})
        error_count += 1
        if verbose:
            print(f"❌ {msg}")

    # 2. 检查缺失的工具
    missing = expected_tools - actual_tools
    for tool in sorted(missing):
        msg = f"[ERROR] 工具 {tool} 在 schema 里但未注册"
        findings.append({"id": f"TOOL-MISSING-{tool}", "level": "error", "msg": msg,
                         "fix": f"在 main._build_registry() 注册 {tool}"})
        error_count += 1
        if verbose:
            print(f"❌ {msg}")

    # 3. 检查多余的工具
    extra = actual_tools - expected_tools
    for tool in sorted(extra):
        msg = f"[WARNING] 工具 {tool} 已注册但不在 schema 里"
        findings.append({"id": f"TOOL-EXTRA-{tool}", "level": "warning", "msg": msg,
                         "fix": f"在 governance/project_schema.json 添加 {tool}"})
        warning_count += 1
        if verbose:
            print(f"⚠️  {msg}")

    if verbose:
        if not missing and not extra and len(actual_tools) == len(expected_tools):
            print(f"✅ 工具 schema 一致: {len(actual_tools)} 个工具")

    return error_count, warning_count, findings


def validate_loop_packages(verbose: bool = True) -> Tuple[int, int, List[dict]]:
    """Validate loop package manifests against runtime registry contracts."""
    sys.path.insert(0, str(PROJECT_ROOT))
    findings: List[dict] = []
    try:
        from loop_packages import validate_all_loop_packages  # noqa: E402
    except Exception as e:
        msg = f"[ERROR] cannot import loop package validator: {e}"
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
        package_count = result.get("package_count", 0)
        packages = ", ".join(result.get("packages", []))
        print(f"✅ loop package contracts consistent: {package_count} packages")
        print(f"   packages: {packages}")

    return len(findings), 0, findings


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    error_count = 0
    warning_count = 0

    valid_commands = {"dirs", "tools", "loop-packages", "all"}
    if command not in valid_commands:
        print(f"Unknown command: {command}")
        print("Expected one of: dirs, tools, loop-packages, all")
        sys.exit(2)

    print(f"📂 工作基址: {BASE_DIR}")
    print(f"📦 项目根: {PROJECT_ROOT}")
    print()

    if command in ("dirs", "all"):
        print("=" * 60)
        print("目录契约校验")
        print("=" * 60)
        e, w, _ = validate_dirs()
        error_count += e
        warning_count += w
        print()

    if command in ("tools", "all"):
        print("=" * 60)
        print("工具 schema 校验")
        print("=" * 60)
        e, w, _ = validate_tools()
        error_count += e
        warning_count += w
        print()

    if command in ("loop-packages", "all"):
        print("=" * 60)
        print("loop package 合约校验")
        print("=" * 60)
        e, w, _ = validate_loop_packages()
        error_count += e
        warning_count += w
        print()

    print("=" * 60)
    print(f"汇总: {error_count} errors, {warning_count} warnings")
    print("=" * 60)

    if error_count > 0:
        sys.exit(2)
    elif warning_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
