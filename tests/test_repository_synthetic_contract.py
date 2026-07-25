from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import subprocess

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
MANAGED_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".ini", ".env", ".txt"}
POLICY_IMPLEMENTATIONS = {
    "governance/repository_hygiene.py",
    "tests/test_repository_hygiene.py",
    "tests/test_repository_synthetic_contract.py",
}
BUSINESS_CONTENT_PREFIXES = ("tests/", "business_rules/", "docs/superpowers/templates/")
SYNTHETIC = re.compile(
    r"^(?:合成|虚构|SYN[-_]|synthetic(?:[-_/]|$)|example\.invalid(?:[/:\s]|$))",
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
        wsl_path = re.fullmatch(r"/mnt/([A-Za-z])/(.*)", raw_git_dir)  # repo-hygiene: allow=synthetic-path
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
        "str", "string", "optional", "null", "true", "false", "project_manager", "customer", "开户银行", "公司",
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
    return normalized in PLACEHOLDERS or bool(SYNTHETIC.match(normalized))


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


def _static_string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string_value(node.left)
        right = _static_string_value(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _assignment_field(target: ast.AST) -> str | None:
    if isinstance(target, ast.Name):
        return target.id.casefold()
    if isinstance(target, ast.Attribute):
        return target.attr.casefold()
    if isinstance(target, ast.Subscript):
        key = _static_string_value(target.slice)
        if key is not None:
            return key.casefold()
    return None


def _literal_bindings(node: ast.AST) -> list[tuple[str, ast.AST, str]]:
    if isinstance(node, ast.Assign):
        value = _static_string_value(node.value)
        if value is None:
            return []
        bindings = []
        for target in node.targets:
            field = _assignment_field(target)
            if field is not None:
                bindings.append((field, node.value, value))
        return bindings
    if isinstance(node, ast.AnnAssign):
        field = _assignment_field(node.target)
        value = _static_string_value(node.value) if node.value is not None else None
        if field is None or value is None or node.value is None:
            return []
        return [(field, node.value, value)]
    if isinstance(node, ast.Dict):
        bindings = []
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                continue
            field = _static_string_value(key_node)
            value = _static_string_value(value_node)
            if field is not None and value is not None:
                bindings.append((field.casefold(), value_node, value))
        return bindings
    if isinstance(node, ast.Call):
        bindings = []
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            value = _static_string_value(keyword.value)
            if value is not None:
                bindings.append((keyword.arg.casefold(), keyword.value, value))
        return bindings
    return []


def _python_violations(relative_path: str, text: str) -> list[str]:
    violations: list[str] = []
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for category in _string_categories(node.value):
                violations.append(f"{relative_path}:{node.lineno}:{category}")
        for field, value_node, value in _literal_bindings(node):
            if field in BUSINESS_FIELDS and not _allowed(value):
                violations.append(
                    f"{relative_path}:{value_node.lineno}:field:{field}"
                )
            if field == "source_kind" and value.strip().casefold() == "real":
                violations.append(
                    f"{relative_path}:{value_node.lineno}:real_source_binding"
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
    plain = json.dumps(
        {"customer_{}".format("name"): "Example Technology " + "Company"}
    )
    synthetic = json.dumps({"customer_name": "合成机构001有限公司"})

    assert _json_violations("tests/fixture.json", plain) == [
        "tests/fixture.json:json_field:customer_name"
    ]
    assert _json_violations("tests/fixture.json", synthetic) == []


@pytest.mark.parametrize(
    "content",
    [
        "project_" + 'name = "Commercial Delivery"',
        "project_" + 'name: str = "Commercial Delivery"',
        'record = {"project_' + 'name": "Commercial Delivery"}',
        "source_" + 'kind = "real"',
        'record = {"source_' + 'kind": "real"}',
        "project_" + 'name = "Customer合成Migration"',
        "self.project_" + 'name = "Commercial Delivery"',
        'record["project_' + 'name"] = "Commercial Delivery"',
        "Fixture(project_" + 'name="Commercial Delivery")',
        "project_" + 'name = "Commercial " + "Delivery"',
        "self.source_" + 'kind = "real"',
        'record["source_' + 'kind"] = "real"',
        "Fixture(source_" + 'kind="real")',
        "source_" + 'kind = "re" + "al"',
    ],
)
def test_python_contract_rejects_literal_business_bindings(content):
    violations = _python_violations("src/business_fixture.py", content)

    assert violations


@pytest.mark.parametrize(
    "content",
    [
        "project_name: str",
        "def load(project_name: str) -> str:\n    return project_name",
        "project_name = record.project_name",
        'project_name = payload["project_name"]',
        'project_name = f"{prefix}-{suffix}"',
        "project_name = Field(default=None)",
        'project_name = "合成项目Alpha"',
        'source_kind = "synthetic"',
        "self.project_name = record.project_name",
        'record["project_name"] = payload[field_name]',
        'Fixture(project_name=f"{prefix}-{suffix}")',
        "Fixture(project_name=build_project_name())",
        "self.source_kind = metadata.source_kind",
        'record["source_kind"] = metadata[kind_key]',
        'Fixture(source_kind=f"{kind}")',
        "Fixture(source_kind=detect_source_kind())",
    ],
)
def test_python_contract_allows_dynamic_or_synthetic_bindings(content):
    assert _python_violations("src/business_fixture.py", content) == []


def test_ocr_baseline_does_not_relabel_historical_rows_as_synthetic():
    baseline = (PROJECT_DIR / "docs/ocr-baseline.md").read_text(encoding="utf-8")
    benchmark_section = baseline.split("## 2.", 1)[1].split("## 4.", 1)[0]

    assert "逐样本历史指标已移除" in benchmark_section
    assert "重新生成的合成基线" in benchmark_section
    assert not re.search(
        r"^\|\s*(?!引擎\b|---|指标\b)[^|]+\|\s*\d+\s*\|",
        benchmark_section,
        re.MULTILINE,
    )
