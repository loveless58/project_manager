from __future__ import annotations

import ast
import codecs
from collections import Counter
import hashlib
import math
import json
import os
from pathlib import Path
import re
import subprocess

import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
MANAGED_SUFFIXES = {
    ".bat", ".cfg", ".cmd", ".conf", ".css", ".csv", ".env", ".htm",
    ".html", ".ini", ".js", ".json", ".md", ".ps1", ".py", ".sh",
    ".sql", ".swift", ".toml", ".tsv", ".txt", ".xml", ".yaml", ".yml",
}
MANAGED_NAMES = {
    ".gitattributes", ".gitignore", "Dockerfile", "LICENSE", "Makefile",
    "NOTICE", "README",
}
MANAGED_EXTENSIONLESS_ROLES = {"config", "governance", "scripts", "skills"}
POLICY_IMPLEMENTATIONS = {
    "governance/repository_hygiene.py",
    "tests/test_repository_hygiene.py",
    "tests/test_repository_hygiene_c2.py",
    "tests/test_repository_hygiene_c3.py",
    "tests/test_repository_synthetic_contract.py",
    "tests/test_final_fix_repository_governance.py",
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
    r"(?<!\\)[\u4e00-\u9fffA-Za-z0-9（）()·]{2,40}(?:"  # repo-hygiene: allow=synthetic-path
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



SWIFT_BRIDGE_PATH = "integrations/macos_vision_bridge/swift_ocr_bridge"
SWIFT_BRIDGE_SHA256 = (
    "384c1fabeaccec7133f1563a9681c2e5dbd1fceee1b22edc9f4138e78bb114f7"
)
SWIFT_BRIDGE_MACHO_ARM64_EXECUTABLE_HEADER = bytes.fromhex(
    "cffaedfe" "0c000001" "00000000" "02000000"
)

_CREDENTIAL_TERMINALS = {
    "token", "secret", "key", "credential", "credentials", "password",
    "passwd", "cookie", "session",
}
_BENIGN_KEY_PREFIXES = {
    "cache", "content", "dictionary", "foreign", "index", "lookup", "object",
    "primary", "public", "schema", "sort",
}
_HIGH_ENTROPY_SECURITY_WORDS = {
    "auth", "authentication", "authorization", "bearer", "encryption", "oauth",
    "signing", "webhook",
}
_SAFE_CREDENTIAL_LITERALS = {
    "dummy-token", "fake-key-for-test", "redacted", "test-key",
}
_TEMPLATE_REFERENCE = re.compile(
    r"(?:\$[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*\}|"
    r"%[A-Za-z_][A-Za-z0-9_]*%|\{\{\s*[A-Za-z_][A-Za-z0-9_.-]*\s*\}\}|"
    r"env:[A-Za-z_][A-Za-z0-9_]*|<[^<>\r\n]+>|"
    r"%\([A-Za-z_][A-Za-z0-9_]*\)s?)",
    re.IGNORECASE,
)
_DIRECT_CREDENTIAL_PATTERNS = (
    re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"),
    re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
    re.compile(
        r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----",
        re.IGNORECASE,
    ),
)
_TEXT_STATIC_ASSIGNMENT = re.compile(
    r"^\s*(?:(?:export|set|const|let|var)\s+|-\s*)?"
    r"(?:[\"'])?(?P<name>[A-Za-z][A-Za-z0-9_.-]*)(?:[\"'])?"
    r"\s*[:=]\s*(?:"
    r"(?P<quote>[\"'])(?P<quoted>[^\"'\r\n]+)(?P=quote)"
    r"|(?P<bare>[^\s#;,()]+)(?=\s*(?:#.*)?$))",
    re.MULTILINE,
)
_INDEPENDENT_JS_LITERAL_BINDING = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=;\r\n]+)?\s*=\s*"
    r"(?P<quote>[\"'`])(?P<value>[^\r\n]*?)(?P=quote)"
    r"\s*;?\s*(?://.*)?$",
    re.MULTILINE,
)
_UNC_PATH = re.compile(
    r"(?<![A-Za-z0-9:/\\])(?:"
    r"\\\\\?\\UNC\\[^\\/\s\"'`?]+\\[^\\/\s\"'`]+"
    r"|\\\\[^\\/?\s\"'`]+\\[^\\/\s\"'`]+"
    r"|//(?![/?])[^/\s\"'`]+/[^/\s\"'`]+)",  # repo-hygiene: allow=synthetic-path
    re.IGNORECASE,
)
_INDEPENDENT_CANONICAL_UNC = re.compile(
    r"(?<![A-Za-z0-9:/])(?://\?/UNC/|//(?![/?]))"  # repo-hygiene: allow=synthetic-path
    r"[^/\s\"'`?]+/[^/\s\"'`]+(?:/[^/\s\"'`]*)?",  # repo-hygiene: allow=synthetic-path
    re.IGNORECASE,
)
_STRICT_SYNTHETIC_PATH_COMMENT = re.compile(
    r"(?:#|//|<!--)\s*repo-hygiene:\s*allow=synthetic-path"
    r"(?:\s*-->)?\s*$",
    re.IGNORECASE,
)


def _independent_is_managed_text(relative_path: str) -> bool:
    path = Path(relative_path)
    if path.suffix.casefold() in MANAGED_SUFFIXES:
        return True
    if path.name in MANAGED_NAMES:
        return True
    return not path.suffix and bool(
        MANAGED_EXTENSIONLESS_ROLES.intersection(path.parts)
    )


def _independent_decode_managed_text(payload: bytes) -> str:
    if payload.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        raise UnicodeDecodeError("utf-32", payload, 0, 4, "UTF-32 is not managed")
    if payload.startswith(codecs.BOM_UTF8):
        text = payload.decode("utf-8-sig")
    elif payload.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = payload.decode("utf-16")
    else:
        text = payload.decode("utf-8")
    if "\0" in text:
        raise UnicodeDecodeError("managed-text", payload, 0, 1, "NUL is forbidden")
    return text


def _independent_file_policy_violation(
    relative_path: str, payload: bytes
) -> str | None:
    normalized = relative_path.replace("\\", "/")
    if normalized == SWIFT_BRIDGE_PATH:
        if not payload.startswith(SWIFT_BRIDGE_MACHO_ARM64_EXECUTABLE_HEADER):
            return "pinned Swift bridge is not a Mach-O arm64 executable"
        if hashlib.sha256(payload).hexdigest() != SWIFT_BRIDGE_SHA256:
            return "pinned Swift bridge SHA-256 mismatch"
        return None
    if not _independent_is_managed_text(normalized):
        return "tracked file type is outside the independent managed-text allowlist"
    try:
        _independent_decode_managed_text(payload)
    except UnicodeDecodeError:
        return "managed tracked text is not safely decodable"
    return None


def _independent_unc_views(line: str):
    yield line
    candidate = line
    for _ in range(3):
        unescaped = candidate.replace("\\\\", "\\")
        if unescaped == candidate:
            return
        yield unescaped
        candidate = unescaped


def _independent_contains_unc(value: str, *, decoded_json: bool = False) -> bool:
    for view in _independent_unc_views(value):
        if _UNC_PATH.search(view):
            return True
        if decoded_json and _INDEPENDENT_CANONICAL_UNC.search(
            view.replace("\\", "/")
        ):
            return True
    return False


def _independent_json_strings(text: str):
    try:
        pending = [json.loads(text)]
    except (TypeError, ValueError, RecursionError):
        return
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


def _independent_unc_violations(relative_path: str, text: str) -> list[str]:
    violations: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if _STRICT_SYNTHETIC_PATH_COMMENT.search(line):
            continue
        if _independent_contains_unc(line):
            violations.append(f"{relative_path}:{line_number}:unc_path")
    if Path(relative_path).suffix.casefold() == ".json" and any(
        _independent_contains_unc(value, decoded_json=True)
        for value in _independent_json_strings(text)
    ):
        violations.append(f"{relative_path}:1:decoded_json_unc_path")
    return violations


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


def _raw_assignment_field(target: ast.AST) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Subscript):
        return _static_string_value(target.slice)
    return None


def _credential_literal_bindings(node: ast.AST) -> list[tuple[str, ast.AST, str]]:
    if isinstance(node, ast.Assign):
        value = _static_string_value(node.value)
        if value is None:
            return []
        return [
            (field, node.value, value)
            for target in node.targets
            if (field := _raw_assignment_field(target)) is not None
        ]
    if isinstance(node, ast.AnnAssign):
        field = _raw_assignment_field(node.target)
        value = _static_string_value(node.value) if node.value is not None else None
        if field is None or value is None or node.value is None:
            return []
        return [(field, node.value, value)]
    if isinstance(node, ast.Dict):
        bindings: list[tuple[str, ast.AST, str]] = []
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                continue
            field = _static_string_value(key_node)
            value = _static_string_value(value_node)
            if field is not None and value is not None:
                bindings.append((field, value_node, value))
        return bindings
    if isinstance(node, ast.Call):
        return [
            (keyword.arg, keyword.value, value)
            for keyword in node.keywords
            if keyword.arg is not None
            if (value := _static_string_value(keyword.value)) is not None
        ]
    return []


def _credential_name_words(name: str) -> list[str]:
    expanded = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    expanded = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", expanded)
    return [
        word.casefold()
        for word in re.split(r"[^A-Za-z0-9]+", expanded)
        if word
    ]


def _is_credential_binding_name(name: str) -> bool:
    words = _credential_name_words(name)
    if not words or words[-1] not in _CREDENTIAL_TERMINALS:
        return False
    if words[-1] == "key" and _BENIGN_KEY_PREFIXES.intersection(words[:-1]):
        return False
    return True


def _is_safe_credential_literal(value: str) -> bool:
    raw = value.strip()
    if raw.casefold() in _SAFE_CREDENTIAL_LITERALS:
        return True
    return bool(_TEMPLATE_REFERENCE.fullmatch(raw))


def _looks_high_entropy_secret(value: str) -> bool:
    raw = value.strip()
    if not 32 <= len(raw) <= 512 or any(character.isspace() for character in raw):
        return False
    if re.fullmatch(r"[0-9a-fA-F]{32,128}", raw):
        return False
    character_classes = sum(
        bool(re.search(pattern, raw))
        for pattern in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]")
    )
    if character_classes < 3:
        return False
    counts = Counter(raw)
    entropy = -sum(
        (count / len(raw)) * math.log2(count / len(raw))
        for count in counts.values()
    )
    return entropy >= 4.0


def _binding_is_credential(name: str, value: str) -> bool:
    if _is_safe_credential_literal(value):
        return False
    if _is_credential_binding_name(name):
        return len(value.strip()) >= 8
    words = set(_credential_name_words(name))
    return bool(words.intersection(_HIGH_ENTROPY_SECURITY_WORDS)) and (
        _looks_high_entropy_secret(value)
    )


_INDEPENDENT_YAML_ENV_FIELD = re.compile(
    r"^(?P<indent>[ \t]*)(?P<dash>-\s+)?"
    r"(?P<field>name|value)\s*:\s*(?:"
    r"(?P<quote>[\"'])(?P<quoted>[^\"'\r\n]+)(?P=quote)"
    r"|(?P<bare>[^\s#;,]+))\s*(?:#.*)?$",
    re.IGNORECASE,
)


def _independent_yaml_item_violations(
    relative_path: str,
    item: dict[str, object] | None,
) -> list[str]:
    if item is None:
        return []
    return [
        f"{relative_path}:{line_number}:credential_binding:{name}"
        for name in item["names"]
        for value, line_number in item["values"]
        if _binding_is_credential(name, value)
    ]


def _independent_yaml_env_credentials(relative_path: str, text: str) -> list[str]:
    violations: list[str] = []
    item: dict[str, object] | None = None
    for line_number, line in enumerate(text.splitlines(), 1):
        field_match = _INDEPENDENT_YAML_ENV_FIELD.match(line)
        if field_match and field_match.group("dash"):
            violations.extend(_independent_yaml_item_violations(relative_path, item))
            item = {
                "column": field_match.start("field"),
                "names": [],
                "values": [],
            }
        elif field_match and item is not None:
            if field_match.start("field") != item["column"]:
                continue
        elif not line.strip() or line.lstrip().startswith("#"):
            continue
        else:
            current_column = len(line) - len(line.lstrip(" \t"))
            if item is not None and current_column <= item["column"]:
                violations.extend(
                    _independent_yaml_item_violations(relative_path, item)
                )
                item = None
            continue

        scalar = field_match.group("quoted") or field_match.group("bare") or ""
        if field_match.group("field").casefold() == "name":
            item["names"].append(scalar)
        else:
            item["values"].append((scalar, line_number))
    violations.extend(_independent_yaml_item_violations(relative_path, item))
    return violations


def _independent_credential_violations(
    relative_path: str, text: str
) -> list[str]:
    violations: list[str] = []
    for pattern in _DIRECT_CREDENTIAL_PATTERNS:
        for match in pattern.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            violations.append(
                f"{relative_path}:{line_number}:credential_signature"
            )
    violations.extend(_independent_yaml_env_credentials(relative_path, text))

    for match in _INDEPENDENT_JS_LITERAL_BINDING.finditer(text):
        if _binding_is_credential(match.group("name"), match.group("value")):
            line_number = text.count("\n", 0, match.start()) + 1
            violations.append(
                f"{relative_path}:{line_number}:credential_binding:{match.group('name')}"
            )

    suffix = Path(relative_path).suffix.casefold()
    if suffix == ".json":
        pairs: list[tuple[str, object]] = []
        duplicate_keys: list[str] = []

        def capture_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
            seen: set[str] = set()
            for field, child in items:
                if field in seen:
                    duplicate_keys.append(field)
                seen.add(field)
                pairs.append((field, child))
            return dict(items)

        def reject_nonstandard_constant(value: str) -> object:
            raise ValueError(f"non-standard JSON constant: {value}")

        try:
            json.loads(
                text,
                object_pairs_hook=capture_pairs,
                parse_constant=reject_nonstandard_constant,
            )
        except (TypeError, ValueError, RecursionError):
            violations.append(f"{relative_path}:1:invalid_json")
            return violations

        violations.extend(
            f"{relative_path}:1:duplicate_json_key:{field}"
            for field in duplicate_keys
        )
        for field, child in pairs:
            if isinstance(child, str) and _binding_is_credential(field, child):
                violations.append(
                    f"{relative_path}:1:credential_binding:{field}"
                )
        return violations

    parsed_python = False
    if suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            pass
        else:
            parsed_python = True
            for node in ast.walk(tree):
                for field, value_node, value in _credential_literal_bindings(node):
                    if _binding_is_credential(field, value):
                        violations.append(
                            f"{relative_path}:{value_node.lineno}:credential_binding:{field}"
                        )

    if not parsed_python:
        for match in _TEXT_STATIC_ASSIGNMENT.finditer(text):
            value = match.group("quoted") or match.group("bare") or ""
            if _binding_is_credential(match.group("name"), value):
                line_number = text.count("\n", 0, match.start()) + 1
                violations.append(
                    f"{relative_path}:{line_number}:credential_binding:{match.group('name')}"
                )
    return violations


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


def test_independent_contract_default_denies_unmanaged_and_sensitive_files(tmp_path):
    bridge = (PROJECT_DIR / "integrations/macos_vision_bridge/swift_ocr_bridge").read_bytes()
    cases = [
        ("customer/contract.pdf", b"%PDF-1.7\x00customer"),
        ("config/server.key", b"-----BEGIN " + b"PRIVATE KEY-----"),
        ("NOTICE.unknown", b"plain text"),
        ("bin/tool", b"\x7fELF\x00binary"),
        ("bin/swift_ocr_bridge", bridge),
    ]

    for relative_path, payload in cases:
        assert _independent_file_policy_violation(relative_path, payload)


def test_independent_contract_exactly_allows_tracked_swift_bridge():
    relative_path = "integrations/macos_vision_bridge/swift_ocr_bridge"
    payload = (PROJECT_DIR / relative_path).read_bytes()

    assert _independent_file_policy_violation(relative_path, payload) is None
    assert _independent_file_policy_violation(relative_path, payload + b"tamper")


@pytest.mark.parametrize(
    "credential_name",
    [
        "NODE_TOKEN",
        "REFRESH_TOKEN",
        "SECRET_KEY",
        "SERVICE_CREDENTIAL",
        "AUTH_COOKIE",
        "USER_SESSION",
        "webhookSecret",
    ],
)
def test_independent_contract_rejects_static_credential_vocabulary(credential_name):
    text = credential_name + ' = "' + "production-value-0123456789" + '"'

    assert _independent_credential_violations("config/provider.py", text)


def test_independent_contract_covers_python_credential_binding_shapes():
    value = "production-value-0123456789"
    contents = [
        "self." + "node_token = " + repr(value),
        "record[" + repr("secret_key") + "] = " + repr(value),
        "record = {" + repr("service_credential") + ": " + repr(value) + "}",
        "Provider(auth_cookie=" + repr(value) + ")",
        "user_session: str = " + repr(value),
    ]

    for content in contents:
        assert _independent_credential_violations("config/provider.py", content), content


def test_independent_contract_preserves_name_boundaries_and_text_shapes():
    value = "production-value-0123456789"
    cases = [
        (".py", "Provider(webhook" + "Secret=" + repr(value) + ")"),
        (".py", "record = {" + repr("webhook" + "Secret") + ": " + repr(value) + "}"),
        (".py", "DB" + "Session = " + repr(value)),
        (".py", "JWT" + "Token = " + repr(value)),
        (".yaml", "- auth_" + "token: " + repr(value)),
        (".sh", "export AUTH_" + "TOKEN=" + repr(value)),
    ]

    for suffix, content in cases:
        assert _independent_credential_violations(
            "config/provider" + suffix, content
        ), content


def test_independent_contract_covers_compact_json_credentials_and_placeholder():
    fixture_value = "-".join(("production", "value", "0123456789"))
    rejected = json.dumps(
        {
            "providers": [
                {"auth_token": fixture_value, "enabled": False}
            ]
        },
        separators=(",", ":"),
    )
    allowed = json.dumps(
        {
            "auth_token": "${AUTH_TOKEN}",
            "content_hash": "sha256:0123456789abcdef",
            "public_key": "ssh-rsa documentation-only",
            "cache_key": "document-version-content-hash",
        },
        separators=(",", ":"),
    )

    assert _independent_credential_violations(
        "config/provider.json", rejected
    )
    assert _independent_credential_violations(
        "config/provider.json", allowed
    ) == []


def _independent_credential_json_parser_edge_case(kind: str) -> str:
    field = '"' + "auth_" + "token" + '"'
    fixture_value = "-".join(("production", "value", "0123456789"))
    static_binding = field + ":" + json.dumps(fixture_value)
    if kind == "duplicate":
        dynamic_binding = field + ":" + json.dumps("${AUTH_TOKEN}")
        return "{" + static_binding + "," + dynamic_binding + "}"
    if kind == "malformed":
        return "{" + static_binding + " garbage}"
    raise AssertionError(f"unknown edge case: {kind}")


@pytest.mark.parametrize("kind", ["duplicate", "malformed"])
def test_independent_json_parser_edges_fail_closed(kind):
    assert _independent_credential_violations(
        "config/provider.json",
        _independent_credential_json_parser_edge_case(kind),
    )


@pytest.mark.parametrize(
    "text",
    [
        'NODE_TOKEN = os.environ["NODE_TOKEN"]',
        'SECRET_KEY = os.getenv("SECRET_KEY")',
        "SERVICE_CREDENTIAL = settings.service_credential",
        'AUTH_COOKIE = "${AUTH_COOKIE}"',
        'token_budget = "120000"',
        'credential_name = "NODE_TOKEN"',
        'public_key = "ssh-rsa documentation-only"',
        'session_timeout = "30-seconds"',
        'cookie_policy = "strict-same-site"',
        'cache_key = "document-version-content-hash"',
    ],
)
def test_independent_contract_allows_dynamic_credentials_and_false_positives(text):
    assert _independent_credential_violations("config/provider.py", text) == []


def test_independent_contract_rejects_prefix_and_high_entropy_secrets():
    secrets = [
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "ghp_" + "A1b2" * 9,
        "github_pat_" + "A1b2" * 10,
        "sk-proj-" + "A1b2" * 10,
        "-----BEGIN " + "PRIVATE KEY-----\nsynthetic-material",
    ]
    for value in secrets:
        assert _independent_credential_violations("docs/provider.md", "value: " + value)

    entropy = "uN4@zQ8#pL2$xR6!vT0%mK7&wC3*eH9?"
    assert _independent_credential_violations(
        "config/provider.py", 'AUTH_MATERIAL = "' + entropy + '"'
    )


def _independent_unc_samples() -> list[str]:
    slash = "\\"
    return [
        slash * 2 + "nas01" + slash + "share" + slash + "customer" + slash + "contract.pdf",
        "//" + "nas01/share/customer/contract.pdf",
        slash * 2 + "?" + slash + "UNC" + slash + "nas01" + slash + "share" + slash + "customer" + slash + "contract.pdf",
    ]


@pytest.mark.parametrize("unc_path", _independent_unc_samples())
def test_independent_contract_rejects_unc_with_strict_line_exemption(unc_path):
    marker = "repo-hygiene: allow=synthetic-path"

    assert _independent_unc_violations("docs/runbook.md", "source: " + unc_path)
    assert _independent_unc_violations(
        "docs/runbook.md", "# " + marker + "\nsource: " + unc_path
    )
    assert _independent_unc_violations(
        "docs/runbook.md", "source: " + unc_path + " # " + marker + "; ignored"
    )
    assert _independent_unc_violations(
        "tests/fixture.md", "source: " + unc_path + " # " + marker
    ) == []


@pytest.mark.parametrize("unc_path", _independent_unc_samples())
def test_independent_contract_rejects_escaped_unc_source_and_json(unc_path):
    escaped_source = "source = " + repr(unc_path)
    declared_synthetic_json = json.dumps(
        {
            "fixture_kind": "synthetic",
            "contains_real_business_data": False,
            "path": unc_path,
        }
    )

    assert _independent_unc_violations("tests/path_source.py", escaped_source)
    assert _independent_unc_violations(
        "tests/fixtures/synthetic_unc.json", declared_synthetic_json
    )


@pytest.mark.parametrize(
    ("relative_path", "content"),
    [
        ("config/provider.py", 'databasePassword = "production-value-0123456789"'),
        ("config/provider.js", 'const servicePassword = "production-value-0123456789";'),
        ("config/provider.js", 'let databasePasswd = "production-value-0123456789";'),
        ("config/provider.js", 'var adminPassword = "production-value-0123456789";'),
        (
            "deploy/provider.yaml",
            "env:\n  - name: SERVICE_PASSWORD\n    value: production-value-0123456789\n",
        ),
    ],
)
def test_independent_contract_rejects_password_js_and_kubernetes_credentials(
    relative_path, content
):
    assert _independent_credential_violations(relative_path, content), content


@pytest.mark.parametrize(
    ("relative_path", "content"),
    [
        ("config/provider.js", 'const servicePassword = process.env.SERVICE_PASSWORD;'),
        ("config/provider.ts", 'let databasePasswd = "${DATABASE_PASSWD}";'),
        (
            "deploy/provider.yaml",
            "env:\n  - name: SERVICE_PASSWORD\n    value: ${SERVICE_PASSWORD}\n",
        ),
        ("config/provider.js", 'const cacheKey = "document-version-content-hash";'),
    ],
)
def test_independent_contract_preserves_dynamic_js_yaml_and_benign_keys(
    relative_path, content
):
    assert _independent_credential_violations(relative_path, content) == []


@pytest.mark.parametrize(
    "encoded_json",
    [
        r'{"path":"\/\/nas01\/share\/customer\/contract.pdf"}',
        r'{"deep":{"items":[{"path":"\u005c\u005cnas01\u005cshare\u005ccustomer\u005ccontract.pdf"}]}}',
        r'{"paths":["\u005c\u005c?\u005cUNC/nas01\u005cshare/customer/contract.pdf"]}',
    ],
)
def test_independent_contract_rejects_decoded_json_unc_variants(encoded_json):
    assert _independent_unc_violations("config/provider.json", encoded_json)


def test_independent_contract_allows_decoded_json_relative_paths():
    encoded_json = r'{"deep":[{"path":"relative\/folder\/contract.pdf"}]}'

    assert _independent_unc_violations("config/provider.json", encoded_json) == []


@pytest.mark.parametrize(
    ("relative_path", "content"),
    [
        (
            "config/provider.js",
            'export const servicePassword = "production-value-0123456789";',
        ),
        (
            "config/provider.js",
            'const servicePassword: string = "production-value-0123456789";',
        ),
        (
            "deploy/provider.yaml",
            "env:\n  - value: production-value-0123456789\n    name: SERVICE_PASSWORD\n",
        ),
    ],
)
def test_independent_rejects_export_typed_js_and_unordered_kubernetes_env(
    relative_path, content
):
    assert _independent_credential_violations(relative_path, content), content


def test_independent_rejects_decoded_json_unc_object_key():
    escape = "\\" + "u005c"
    encoded_json = (
        '{"'
        + escape
        + escape
        + "nas01"
        + escape
        + "share"
        + escape
        + 'customer":1}'
    )

    assert _independent_unc_violations("config/provider.json", encoded_json)


def test_all_tracked_files_match_independent_default_deny_contract():
    violations = []
    tracked_files = _tracked_files()
    assert tracked_files
    assert SWIFT_BRIDGE_PATH in tracked_files

    for relative_path in tracked_files:
        payload = (PROJECT_DIR / relative_path).read_bytes()
        violation = _independent_file_policy_violation(relative_path, payload)
        if violation:
            violations.append(f"{relative_path}:{violation}")

    assert violations == []


def test_all_tracked_managed_text_has_no_independent_secret_or_unc_violation():
    violations: list[str] = []
    scanned_paths: list[str] = []
    tracked_files = _tracked_files()
    for relative_path in tracked_files:
        if relative_path in POLICY_IMPLEMENTATIONS or relative_path == SWIFT_BRIDGE_PATH:
            continue
        payload = (PROJECT_DIR / relative_path).read_bytes()
        if _independent_file_policy_violation(relative_path, payload):
            continue
        text = _independent_decode_managed_text(payload)
        scanned_paths.append(relative_path)
        violations.extend(
            _independent_credential_violations(relative_path, text)
        )
        violations.extend(_independent_unc_violations(relative_path, text))

    assert scanned_paths
    assert len(scanned_paths) == len(tracked_files) - len(POLICY_IMPLEMENTATIONS) - 1
    assert not sorted(set(violations)), "\n".join(sorted(set(violations)))
