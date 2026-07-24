from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import subprocess


PROJECT_DIR = Path(__file__).resolve().parents[1]
MANAGED_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".ini", ".env", ".txt"}
POLICY_IMPLEMENTATIONS = {
    "governance/repository_hygiene.py",
    "tests/test_repository_hygiene.py",
    "tests/test_repository_synthetic_contract.py",
}
BUSINESS_CONTENT_PREFIXES = ("tests/", "business_rules/", "docs/superpowers/templates/")
SYNTHETIC = re.compile(
    r"(?:合成|虚构|SYN[-_]|synthetic|example\.invalid)",
    re.IGNORECASE,
)
PLACEHOLDERS = {
    "unknown",
    "none",
    "n/a",
    "未知",
    "待确认",
    "待填写",
    "未提供",
    "无",
    "空",
}
BUSINESS_FIELDS = {
    "customer_name",
    "client_name",
    "buyer",
    "seller",
    "project_name",
    "subject_name",
    "project_id",
    "project_number",
    "sales_owner",
    "contact_name",
    "invoice_number",
    "invoice_id",
    "ticket_number",
    "ticket_id",
    "contract_number",
    "contract_id",
}
ORGANIZATION = re.compile(
    r"(?<!\\)[\u4e00-\u9fffA-Za-z0-9（）()·]{2,40}(?:"
    + "股份有限" + "公司|有限责任" + "公司|有限" + "公司|公司|集团|银行|研究院|大学)"
)
LABELED_VALUE = re.compile(
    r"(?:客户(?:名称)?|招标人/客户|采购人|甲方|乙方|买受人|出卖人|签约主体|"
    r"项目(?:名称|编号|记录)|销售负责人|负责销售|负责人|联系人|"
    r"合同编号|发票(?:号码|号)|票号|订单号|统一社会信用代码)"
    r"\s*[：:]\s*(?P<value>\{[^{}\r\n]+\}|"
    r"(?:(?!\\n)[^\"'\r\n|,，;；]){2,80})",
    re.IGNORECASE,
)
UNQUOTED_FIELD = re.compile(
    r"\b(?:customer_name|client_name|buyer|seller|project_name|project_id|"
    r"subject_name|project_number|sales_owner|contact_name|invoice_number|invoice_id|"
    r"ticket_number|ticket_id|contract_number|contract_id)\b"
    r"\s*[:=]\s*(?P<value>[^\r\n,，;；#}\]]{2,80})", re.IGNORECASE
)
LABELED_IDENTIFIER = re.compile(
    r"(?:合同编号|发票(?:号码|号)|票号|订单号|统一社会信用代码)"
    r"\s*[：:=]\s*(?P<value>[A-Za-z0-9_-]{4,})",
    re.IGNORECASE,
)
STANDALONE_BUSINESS_ID = re.compile(r"\b(?:C\d{7,}|[A-Z]{2,}\d{6,})\b")
REAL_SOURCE_CLAIM = re.compile(
    r"(?:数据来源|来源类型|fixture_kind|source_kind)\s*[：:=]"
    r"[^\r\n]{0,80}(?:真实|real)", re.IGNORECASE
)


def _tracked_files() -> list[str]:
    environment = os.environ.copy()
    marker = PROJECT_DIR / ".git"
    if marker.is_file():
        raw_git_dir = marker.read_text(encoding="utf-8").split(":", 1)[1].strip()
        wsl_path = re.fullmatch(r"/mnt/([A-Za-z])/(.*)", raw_git_dir)
        if os.name == "nt" and wsl_path:
            raw_git_dir = f"{wsl_path.group(1).upper()}:/{wsl_path.group(2)}"
        environment["GIT_DIR"] = raw_git_dir
        environment["GIT_WORK_TREE"] = str(PROJECT_DIR)
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_DIR,
        env=environment,
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in completed.stdout.split(b"\0")
        if item
    ]


def _allowed(value: str) -> bool:
    normalized = value.strip().strip("`*_#-:：.。\"'").casefold()
    if not normalized:
        return True
    if re.fullmatch(r"\{[^{}\r\n]+\}", normalized):
        return True
    if normalized in {
        "str", "string", "optional", "null", "true", "false", "project_manager", "开户银行", "公司",
        "有限公司", "有限责任公司", "股份有限公司", "集团", "银行", "研究院", "大学",
    }:
        return True
    raw = value.strip()
    if re.fullmatch(r"[\"']{2}[)\]}]*", raw):
        return True
    if re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+[\"'`)]*",
        raw.strip("\"'`"),
    ):
        return True
    if re.match(r"[A-Za-z_][A-Za-z0-9_]*\s*[\[(]", raw.strip("\"'`")):
        return True
    return normalized in PLACEHOLDERS or bool(SYNTHETIC.search(value))


def _string_categories(value: str) -> set[str]:
    categories: set[str] = set()
    for match in ORGANIZATION.finditer(value):
        if not _allowed(match.group(0)):
            categories.add("organization")
    for match in LABELED_VALUE.finditer(value):
        if not _allowed(match.group("value")):
            categories.add("labeled_value")
    for match in LABELED_IDENTIFIER.finditer(value):
        if not _allowed(match.group("value")):
            categories.add("business_id")
    for match in STANDALONE_BUSINESS_ID.finditer(value):
        if not _allowed(match.group(0)):
            categories.add("business_id")
    if REAL_SOURCE_CLAIM.search(value):
        categories.add("real_source_claim")
    return categories


def _python_violations(relative_path: str, text: str) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for category in _string_categories(node.value):
                violations.append(f"{relative_path}:{node.lineno}:{category}")
        if (
            not isinstance(node, ast.Dict)
            or not relative_path.startswith(BUSINESS_CONTENT_PREFIXES)
        ):
            continue
        for key_node, value_node in zip(node.keys, node.values):
            if not (
                isinstance(key_node, ast.Constant)
                and isinstance(key_node.value, str)
                and key_node.value.casefold() in BUSINESS_FIELDS
                and isinstance(value_node, ast.Constant)
                and isinstance(value_node.value, str)
            ):
                continue
            if not _allowed(value_node.value):
                violations.append(
                    f"{relative_path}:{value_node.lineno}:field:{key_node.value.casefold()}"
                )
    return violations


def _text_violations(relative_path: str, text: str) -> list[str]:
    violations: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for category in _string_categories(line):
            violations.append(f"{relative_path}:{line_number}:{category}")
        for match in UNQUOTED_FIELD.finditer(line):
            if not _allowed(match.group("value")):
                violations.append(f"{relative_path}:{line_number}:unquoted_field")
    return violations


def _json_violations(relative_path: str, text: str) -> list[str]:
    violations = _text_violations(relative_path, text)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    isinstance(key, str)
                    and key.casefold() in BUSINESS_FIELDS
                    and isinstance(child, str)
                    and not _allowed(child)
                ):
                    violations.append(
                        f"{relative_path}:json_field:{key.casefold()}"
                    )
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(json.loads(text))
    return violations


def test_all_tracked_business_examples_are_independently_synthetic():
    violations: list[str] = []
    for relative_path in _tracked_files():
        if relative_path in POLICY_IMPLEMENTATIONS:
            continue
        path = PROJECT_DIR / relative_path
        if path.suffix.casefold() not in MANAGED_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if path.suffix.casefold() == ".py":
            violations.extend(_python_violations(relative_path, text))
        elif path.suffix.casefold() == ".json":
            violations.extend(_json_violations(relative_path, text))
        else:
            violations.extend(_text_violations(relative_path, text))

    assert not sorted(set(violations)), "\n".join(sorted(set(violations)))


def test_json_contract_rejects_plain_business_values_and_allows_synthetic():
    plain = json.dumps({"customer_name": "Example Technology Company"})
    synthetic = json.dumps({"customer_name": "合成机构001有限公司"})

    assert _json_violations("tests/fixture.json", plain) == [
        "tests/fixture.json:json_field:customer_name"
    ]
    assert _json_violations("tests/fixture.json", synthetic) == []
