"""Repository hygiene checks bounded strictly by Git's tracked-file index."""
from __future__ import annotations

import ast
import codecs
from collections import Counter
from dataclasses import dataclass
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
import warnings
from typing import Iterable, Iterator, List, Mapping, Optional
from urllib.parse import unquote, urlsplit


_HTTP_URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_FILE_URI = re.compile(r"file:[^\s<>\"'`]+", re.IGNORECASE)
_RFC1918_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_POSIX_PERSONAL_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])/(?:Users|home)/[^/\s\"']+(?:/[^\s\"']*)?"
)
_WINDOWS_PERSONAL_PATH = re.compile(
    r"\b[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+(?:[\\/][^\s\"']*)?",
    re.IGNORECASE,
)
# Require a root, nested subject, and file so generic installation
# examples are not misclassified as business-data locations.
_WINDOWS_BUSINESS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])"
    r"[A-Za-z]:[\\/]+(?:[^\\/\s\"'`]+[\\/]+){2,}[^\\/\s\"'`]+"
)
_POSIX_BUSINESS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])"
    r"/(?:Volumes|mnt|media|srv|volume[0-9]+)/(?:[^/\s\"'`]+/){1,}[^/\s\"'`]+",
    re.IGNORECASE,
)
_UNC_BACKSLASH_PATH = re.compile(
    r"(?<![A-Za-z0-9\\])"
    r"(?:\\\\\?\\UNC\\|\\\\)"  # repo-hygiene: allow=synthetic-path
    r"[^\\/\s\"'`?]+\\[^\\/\s\"'`]+"
    r"(?:\\[^\\/\s\"'`]*)?",
    re.IGNORECASE,
)
_UNC_FORWARD_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])"
    r"//(?![/?])"  # repo-hygiene: allow=synthetic-path
    r"[^/\s\"'`]+/[^/\s\"'`]+"
    r"(?:/[^/\s\"'`]*)?",
    re.IGNORECASE,
)
_UNC_CANONICAL_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])"
    r"(?://\?/UNC/|//(?![/?]))"  # repo-hygiene: allow=synthetic-path
    r"[^/\s\"'`?]+/[^/\s\"'`]+"
    r"(?:/[^/\s\"'`]*)?",
    re.IGNORECASE,
)
_CREDENTIAL_DSN = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|amqps?)://"
    r"[^:/\s]+:(?P<secret>[^@/\s]+)@",
    re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?:[\"'])?\b(?:[A-Za-z][A-Za-z0-9_-]*[_-])?"
    r"(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd)\b"
    r"(?:[\"'])?\s*[:=]\s*(?:[rubf]{0,2})?"
    r"(?:[\"'](?P<quoted>[^\"'\r\n]{8,})[\"']|"
    r"(?P<bare>[^\s#;,()]{8,})(?=\s*(?:#.*)?$))",
    re.IGNORECASE | re.MULTILINE,
)
_BEARER_CREDENTIAL = re.compile(
    r"\bAuthorization\s*:\s*Bearer\s+(?P<secret>[A-Za-z0-9._~-]{16,})",
    re.IGNORECASE,
)
_DIRECT_API_CREDENTIAL = re.compile(
    r"\b(?P<secret>(?:sk|pk)-(?:live|prod)-[A-Za-z0-9_-]{16,})\b",
    re.IGNORECASE,
)
_AWS_ACCESS_KEY = re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")
_GITHUB_CREDENTIAL = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"
)
_OPENAI_CREDENTIAL = re.compile(
    r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"
)
_PRIVATE_KEY_HEADER = re.compile(
    r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----",
    re.IGNORECASE,
)
_STATIC_TEXT_BINDING = re.compile(
    r"^\s*(?:(?:export|set|const|let|var)\s+|-\s*)?"
    r"(?:[\"'])?(?P<name>[A-Za-z][A-Za-z0-9_.-]*)(?:[\"'])?"
    r"\s*[:=]\s*(?:"
    r"(?P<quote>[\"'])(?P<quoted>[^\"'\r\n]+)(?P=quote)"
    r"|(?P<bare>[^\s#;,()]+)(?=\s*(?:#.*)?$))",
    re.MULTILINE,
)
_JS_LITERAL_BINDING = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"(?:\s*:\s*[^=;\r\n]+)?\s*=\s*"
    r"(?P<quote>[\"'`])(?P<value>[^\r\n]*?)(?P=quote)"
    r"(?:\s+(?:as\s+(?:const|[A-Za-z_$][A-Za-z0-9_$]*"
    r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*(?:\s*<[^;=\r\n]+>)?"
    r"(?:\s*\[\])*)|satisfies\s+[A-Za-z_$][^;\r\n]*))?"
    r"\s*;?\s*(?://.*)?$",
    re.MULTILINE,
)
_JS_ENV_TEMPLATE = re.compile(
    r"\$\{\s*(?:process|import\.meta)\.env\."
    r"[A-Za-z_$][A-Za-z0-9_$]*\s*\}"
)
_CREDENTIAL_TERMINALS = {
    "token",
    "secret",
    "key",
    "credential",
    "credentials",
    "password",
    "passwd",
    "cookie",
    "session",
}
_BENIGN_KEY_PREFIXES = {
    "cache", "content", "dictionary", "foreign", "index", "lookup",
    "object", "primary", "public", "schema", "sort",
}
_HIGH_ENTROPY_SECURITY_WORDS = {
    "auth", "authentication", "authorization", "bearer", "encryption",
    "oauth", "signing", "webhook",
}
_REAL_SAMPLE_HINT = re.compile(
    r"(?:真实样本|real[_ -]?samples?|customer[_ -]?samples?|production[_ -]?samples?)",
    re.IGNORECASE,
)
_EMBEDDED_COLLECTION = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\s*:[^=\r\n]+)?\s*=\s*[\[\{(]",
    re.MULTILINE,
)
_JSON_SAMPLE_LIST = re.compile(r'[\"\']samples[\"\']\s*:\s*\[', re.IGNORECASE)
_SAMPLE_PATH_FIELD = re.compile(
    r"\b(?:relative_path|source_file|source_path|filename|file_path)\b",
    re.IGNORECASE,
)
_SAMPLE_EXPECTATION_FIELD = re.compile(
    r"\b(?:expected_category|expected_document_type|expected_type|category)\b",
    re.IGNORECASE,
)
_SAMPLE_DOCUMENT_ENTRY = re.compile(
    r"[^\s\"']+\.(?:pdf|docx?|xlsx?|pptx?|png|jpe?g|md|txt|xml|json)",
    re.IGNORECASE,
)
_STRUCTURED_BUSINESS_VALUE = re.compile(
    r"(?:[\"'](?P<field>customer_name|client_name|project_name|subject_name|project_id|"
    r"project_number|sales_owner|contact_name|invoice_number|invoice_id|"
    r"ticket_number|ticket_id|source_file|source_path)[\"']\s*[:=]\s*"
    r"[\"'](?P<quoted>[^\"'\r\n]{2,})[\"'])"
    r"|(?:"
    r"(?P<label>客户(?:名称)?|项目(?:名称|编号|记录)|销售负责人|联系人|"
    r"发票(?:号码|号)|票号|来源文件|文件名)\s*[：:]\s*"
    r"(?P<label_value>\{[^{}\r\n]+\}|"
    r"(?:(?!\\n)[^\r\n|,，;；\"']){2,})"  # repo-hygiene: allow=synthetic-path
    r")",
    re.IGNORECASE,
)
_UNQUOTED_STRUCTURED_BUSINESS_VALUE = re.compile(
    r"\b(?P<field>customer_name|client_name|buyer|seller|project_name|subject_name|"
    r"project_id|project_number|sales_owner|contact_name|invoice_number|"
    r"invoice_id|ticket_number|ticket_id|contract_number|contract_id)\b"
    r"\s*[:=]\s*(?P<value>[^\r\n,，;；#}\]]{2,80})",
    re.IGNORECASE,
)
_ORGANIZATION_BUSINESS_VALUE = re.compile(
    r"(?<!\\)[\u4e00-\u9fffA-Za-z0-9（）()·]{2,40}"  # repo-hygiene: allow=synthetic-path
    r"(?:股份有限" + r"公司|有限责任" + r"公司|有限" + r"公司|公司|"
    r"集团|银行|研究院|大学)"
)
_LABELED_BUSINESS_IDENTIFIER = re.compile(
    r"(?:合同编号|发票(?:号码|号)|票号|订单号|统一社会信用代码|"
    r"invoice_(?:number|id)|ticket_(?:number|id)|contract_(?:number|id))"
    r"\s*[：:=]\s*[\"']?(?P<value>[A-Za-z0-9_-]{4,})",
    re.IGNORECASE,
)
_LABELED_BUSINESS_VALUE = re.compile(
    r"(?:客户(?:名称)?|招标人/客户|采购人|甲方|乙方|买受人|出卖人|签约主体|"
    r"项目(?:名称|编号|记录)|销售负责人|负责销售|负责人|联系人)"
    r"\s*[：:]\s*(?P<value>\{[^{}\r\n]+\}|"
    r"(?:(?!\\n)[^\"'\r\n|,，;；]){2,80})",
    re.IGNORECASE,
)
_REAL_SOURCE_CLAIM = re.compile(
    r"(?:数据来源|来源类型|fixture_kind|source_kind)\s*[：:=]"
    r"[^\"'\r\n]{0,80}(?:真实|real)", re.IGNORECASE
)
_SYNTHETIC_BUSINESS_VALUE = re.compile(
    r"^(?:合成|虚构|SYN[-_]|synthetic(?:[-_/]|$)|example\.invalid(?:[/:\s]|$))",
    re.IGNORECASE,
)
_PYTHON_BUSINESS_FIELDS = {
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
_BUSINESS_CONTENT_PLACEHOLDERS = {
    "unknown",
    "none",
    "n/a",
    "未知",
    "待确认",
    "未提供",
    "无",
    "空",
}
_SYNTHETIC_PATH_EXEMPTION = "repo-hygiene: allow=synthetic-path"
_SYNTHETIC_PATH_EXEMPTION_LINE = re.compile(
    r"(?:#|//|<!--)\s*repo-hygiene:\s*allow=synthetic-path"
    r"(?:\s*-->)?\s*$",
    re.IGNORECASE,
)
_SYNTHETIC_DATA_DECLARATION = "repo-hygiene: data=synthetic"
_BUSINESS_KB_PATH = re.compile(r"^business_rules/(?:[^/]+/)*kb\.md$")
_SENSITIVE_TRACKED_SUFFIXES = {
    ".7z", ".cer", ".crt", ".db", ".der", ".doc", ".docx", ".gif",
    ".gz", ".jks", ".jpeg", ".jpg", ".key", ".keystore", ".p12",
    ".pfx", ".pem", ".pdf", ".png", ".ppt", ".pptx", ".rar", ".sqlite",
    ".sqlite3", ".tar", ".tif", ".tiff", ".xls", ".xlsx", ".zip",
}
_SWIFT_BRIDGE_PATH = "integrations/macos_vision_bridge/swift_ocr_bridge"
_SWIFT_BRIDGE_SHA256 = (
    "384c1fabeaccec7133f1563a9681c2e5dbd1fceee1b22edc9f4138e78bb114f7"
)
# Mach-O 64-bit little-endian, CPU_TYPE_ARM64, subtype 0, MH_EXECUTE.
_SWIFT_BRIDGE_HEADER = bytes.fromhex(
    "cffaedfe" "0c000001" "00000000" "02000000"
)
_MANAGED_TEXT_SUFFIXES = {
    ".bat",
    ".cfg",
    ".cmd",
    ".conf",
    ".css",
    ".csv",
    ".env",
    ".htm",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
_MANAGED_TEXT_NAMES = {
    ".gitattributes",
    ".gitignore",
    "Dockerfile",
    "LICENSE",
    "Makefile",
    "NOTICE",
    "README",
}
_MANAGED_TEXT_ROLES = {"config", "governance", "scripts", "skills"}


@dataclass(frozen=True)
class RepositoryHygieneResult:
    errors: int
    warnings: int
    findings: List[dict]
    tracked_count: int
    scanned_text_count: int
    skipped_binary_count: int
    skipped_non_target_count: int
    decode_error_count: int

    def __iter__(self) -> Iterator[object]:
        """Preserve the existing three-value governance unpacking contract."""
        yield self.errors
        yield self.warnings
        yield self.findings


class _ManagedTextDecodeError(ValueError):
    """Managed text uses an unsupported or unsafe decoded representation."""


def _git_environment(project_root: Path) -> Mapping[str, str]:
    environment = os.environ.copy()
    marker = project_root / ".git"
    if not marker.is_file():
        return environment
    marker_text = marker.read_text(encoding="utf-8").strip()
    if not marker_text.startswith("gitdir:"):
        return environment
    raw_git_dir = marker_text.split(":", 1)[1].strip()
    wsl_path = re.fullmatch(r"/mnt/([A-Za-z])/(.*)", raw_git_dir)  # repo-hygiene: allow=synthetic-path
    if os.name == "nt" and wsl_path:
        raw_git_dir = f"{wsl_path.group(1).upper()}:/{wsl_path.group(2)}"
    environment["GIT_DIR"] = raw_git_dir
    environment["GIT_WORK_TREE"] = str(project_root)
    return environment


def tracked_repository_files(project_root: Path) -> List[str]:
    """Return the exact Git tracked-file boundary for hygiene checks."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=project_root,
        env=_git_environment(project_root),
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]


def _finding(finding_id: str, path: str, message: str) -> dict:
    return {
        "id": finding_id,
        "level": "error",
        "msg": f"[ERROR] {path}: {message}",
    }


def _is_managed_text(relative_path: str) -> bool:
    path = Path(relative_path)
    if path.suffix.lower() in _MANAGED_TEXT_SUFFIXES:
        return True
    if path.name in _MANAGED_TEXT_NAMES:
        return True
    return not path.suffix and bool(_MANAGED_TEXT_ROLES.intersection(path.parts))


def _decode_managed_text(payload: bytes) -> str:
    if payload.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        raise _ManagedTextDecodeError("UTF-32 is not a managed text encoding")
    if payload.startswith(codecs.BOM_UTF8):
        text = payload.decode("utf-8-sig")
    elif payload.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = payload.decode("utf-16")
    else:
        text = payload.decode("utf-8")
    if "\0" in text:
        raise _ManagedTextDecodeError("managed text contains a NUL character")
    return text


def _non_target_is_binary(payload: bytes) -> bool:
    if b"\0" in payload:
        return True
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _contains_rfc1918_url(text: str) -> bool:
    for match in _HTTP_URL.finditer(text):
        try:
            hostname = urlsplit(match.group(0)).hostname
            address = ipaddress.ip_address(hostname) if hostname else None
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv4Address) and any(
            address in network for network in _RFC1918_NETWORKS
        ):
            return True
    return False


def _overlaps_http_url(match: re.Match[str], http_spans: List[tuple[int, int]]) -> bool:
    start, end = match.span()
    return any(start < http_end and end > http_start for http_start, http_end in http_spans)


def _contains_personal_path(text: str) -> bool:
    http_spans = [match.span() for match in _HTTP_URL.finditer(text)]
    for pattern in (_POSIX_PERSONAL_PATH, _WINDOWS_PERSONAL_PATH):
        if any(
            not _overlaps_http_url(match, http_spans)
            for match in pattern.finditer(text)
        ):
            return True

    for match in _FILE_URI.finditer(text):
        try:
            uri_path = unquote(urlsplit(match.group(0)).path)
        except ValueError:
            continue
        if _POSIX_PERSONAL_PATH.search(uri_path) or _WINDOWS_PERSONAL_PATH.search(
            uri_path
        ):
            return True
    return False


def _is_declared_synthetic_fixture(relative_path: str, text: str) -> bool:
    path = Path(relative_path)
    normalized = relative_path.replace("\\", "/")
    if not normalized.startswith("tests/fixtures/"):
        return False
    if path.suffix.lower() != ".json" or not path.name.startswith("synthetic_"):
        return False
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("fixture_kind") == "synthetic"
        and payload.get("contains_real_business_data") is False
    )


def _unc_scan_views(line: str) -> Iterator[str]:
    """Yield source text plus bounded unescape layers for Python/JSON literals."""
    yield line
    candidate = line
    for _ in range(3):
        unescaped = candidate.replace("\\\\", "\\")
        if unescaped == candidate:
            return
        yield unescaped
        candidate = unescaped


def _contains_unc_path(value: str, *, decoded_json: bool = False) -> bool:
    for view in _unc_scan_views(value):
        if _UNC_BACKSLASH_PATH.search(view) or _UNC_FORWARD_PATH.search(view):
            return True
        if decoded_json and _UNC_CANONICAL_PATH.search(
            view.replace("\\", "/")
        ):
            return True
    return False


def _decoded_json_strings(text: str) -> Iterator[str]:
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


def _contains_business_absolute_path(text: str, synthetic_fixture: bool) -> bool:
    for line in text.splitlines():
        strictly_exempt = bool(_SYNTHETIC_PATH_EXEMPTION_LINE.search(line))
        if strictly_exempt:
            continue
        if _contains_unc_path(line):
            return True
        if synthetic_fixture:
            continue
        for pattern in (
            _WINDOWS_BUSINESS_ABSOLUTE_PATH,
            _POSIX_BUSINESS_ABSOLUTE_PATH,
        ):
            for match in pattern.finditer(line):
                value = match.group(0)
                if _POSIX_PERSONAL_PATH.search(value) or _WINDOWS_PERSONAL_PATH.search(
                    value
                ):
                    continue
                return True
    for value in _decoded_json_strings(text):
        if _contains_unc_path(value, decoded_json=True):
            return True
    return False


_YAML_ENV_ITEM_FIELD = re.compile(
    r"^(?P<indent>[ \t]*)(?P<dash>-\s+)?"
    r"(?P<field>name|value)\s*:\s*(?:"
    r"(?P<quote>[\"'])(?P<quoted>[^\"'\r\n]+)(?P=quote)"
    r"|(?P<bare>[^\r\n]*?))(?:[ \t]+#.*)?[ \t]*$",
    re.IGNORECASE,
)


def _yaml_item_contains_credential(item: Optional[Dict[str, Any]]) -> bool:
    if item is None:
        return False
    return any(
        _binding_contains_credential(name, value, quoted=quoted)
        for name in item["names"]
        for value, quoted in item["values"]
    )


def _contains_yaml_env_credential(text: str) -> bool:
    item: Optional[Dict[str, Any]] = None
    for line in text.splitlines():
        field_match = _YAML_ENV_ITEM_FIELD.match(line)
        if field_match and field_match.group("dash"):
            if _yaml_item_contains_credential(item):
                return True
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
                if _yaml_item_contains_credential(item):
                    return True
                item = None
            continue

        scalar = (
            field_match.group("quoted") or field_match.group("bare") or ""
        ).strip()
        if field_match.group("field").casefold() == "name":
            item["names"].append(scalar)
        else:
            item["values"].append(
                (scalar, field_match.group("quoted") is not None)
            )
    return _yaml_item_contains_credential(item)


_PLACEHOLDER_SECRETS = {
    "secret",
    "secret-pass",
    "password",
    "fake-key-for-test",
    "test-key",
    "dummy-token",
    "redacted",
}


def _is_placeholder_secret(value: str, *, quoted: bool = False) -> bool:
    raw = value.strip()
    normalized = raw.casefold()
    if normalized in _PLACEHOLDER_SECRETS:
        return True
    template_references = (
        r"\$[A-Za-z_][A-Za-z0-9_]*",
        r"\$\{[A-Za-z_][A-Za-z0-9_]*\}",
        r"%[A-Za-z_][A-Za-z0-9_]*%",
        r"\{\{\s*[A-Za-z_][A-Za-z0-9_.-]*\s*\}\}",
        r"env:[A-Za-z_][A-Za-z0-9_]*",
        r"<[^<>\r\n]+>",
        r"%\([A-Za-z_][A-Za-z0-9_]*\)s?",
    )
    if any(re.fullmatch(pattern, raw, re.IGNORECASE) for pattern in template_references):
        return True
    if quoted:
        return False
    python_references = (
        r"os\.environ\[\s*[\"'][A-Za-z_][A-Za-z0-9_]*[\"']\s*\]",
        r"os\.getenv\(\s*[\"'][A-Za-z_][A-Za-z0-9_]*[\"']\s*\)",
        r"settings\.[A-Za-z_][A-Za-z0-9_]*",
        r"[A-Za-z_][A-Za-z0-9_.]*\[[^\]\r\n]+\]",
    )
    return any(re.fullmatch(pattern, raw) for pattern in python_references)


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
    if words[-1] == "key" and any(word in _BENIGN_KEY_PREFIXES for word in words[:-1]):
        return False
    return True


def _has_high_entropy_security_context(name: str) -> bool:
    return bool(_HIGH_ENTROPY_SECURITY_WORDS.intersection(_credential_name_words(name)))


def _looks_high_entropy_secret(value: str) -> bool:
    raw = value.strip()
    if not 32 <= len(raw) <= 512 or any(character.isspace() for character in raw):
        return False
    if re.fullmatch(r"[0-9a-fA-F]{32,128}", raw):
        return False
    classes = sum(
        bool(pattern.search(raw))
        for pattern in (
            re.compile(r"[a-z]"),
            re.compile(r"[A-Z]"),
            re.compile(r"[0-9]"),
            re.compile(r"[^A-Za-z0-9]"),
        )
    )
    if classes < 3:
        return False
    counts = Counter(raw)
    entropy = -sum(
        (count / len(raw)) * math.log2(count / len(raw))
        for count in counts.values()
    )
    return entropy >= 4.0


def _binding_contains_credential(name: str, value: str, *, quoted: bool) -> bool:
    if _is_placeholder_secret(value, quoted=quoted):
        return False
    if _is_credential_binding_name(name):
        return len(value.strip()) >= 8
    return _has_high_entropy_security_context(name) and _looks_high_entropy_secret(value)


def _analyze_json_document(text: str) -> tuple[bool, bool]:
    """Return ``(invalid_json, contains_static_credential)``.

    ``object_pairs_hook`` deliberately observes object members before a mapping
    can discard duplicate names.  Duplicate names and non-standard numeric
    constants are rejected so tracked JSON cannot hide an earlier credential
    behind a later placeholder or rely on Python's permissive JSON extensions.
    """

    pairs: list[tuple[str, object]] = []
    duplicate_key = False

    def capture_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal duplicate_key
        seen: set[str] = set()
        for field, value in items:
            if field in seen:
                duplicate_key = True
            seen.add(field)
            pairs.append((field, value))
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
        return True, False

    contains_credential = any(
        isinstance(value, str)
        and _binding_contains_credential(field, value, quoted=True)
        for field, value in pairs
    )
    return duplicate_key, contains_credential


def _contains_static_python_credential(text: str) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        for field, value in _python_literal_business_bindings(node):
            if _binding_contains_credential(field, value, quoted=True):
                return True
    return False


def _contains_hardcoded_credential(text: str) -> bool:
    for pattern in (
        _AWS_ACCESS_KEY,
        _GITHUB_CREDENTIAL,
        _OPENAI_CREDENTIAL,
        _PRIVATE_KEY_HEADER,
    ):
        if pattern.search(text):
            return True

    if _contains_static_python_credential(text):
        return True

    if _contains_yaml_env_credential(text):
        return True

    for match in _JS_LITERAL_BINDING.finditer(text):
        if match.group("quote") == "`" and _JS_ENV_TEMPLATE.fullmatch(
            match.group("value").strip()
        ):
            continue
        if _binding_contains_credential(
            match.group("name"), match.group("value"), quoted=True
        ):
            return True

    for match in _STATIC_TEXT_BINDING.finditer(text):
        value = match.group("quoted") or match.group("bare") or ""
        if _binding_contains_credential(
            match.group("name"),
            value,
            quoted=match.group("quoted") is not None,
        ):
            return True

    for pattern in (
        _CREDENTIAL_DSN,
        _CREDENTIAL_ASSIGNMENT,
        _BEARER_CREDENTIAL,
        _DIRECT_API_CREDENTIAL,
    ):
        for match in pattern.finditer(text):
            groups = match.groupdict()
            secret = (
                groups.get("secret") or groups.get("quoted") or groups.get("bare")
                or match.group(0)
            )
            if not _is_placeholder_secret(
                secret, quoted=groups.get("quoted") is not None
            ):
                return True
    return False


def _contains_real_sample_manifest(relative_path: str, text: str) -> bool:
    if relative_path.casefold().endswith(".json"):
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and payload.get("fixture_kind") == "local_business":
            return True

    evidence = f"{relative_path}\n{text}"
    if not _REAL_SAMPLE_HINT.search(evidence):
        return False
    has_field_pair = bool(
        _SAMPLE_PATH_FIELD.search(text) and _SAMPLE_EXPECTATION_FIELD.search(text)
    )
    if relative_path.casefold().endswith(".json") and _JSON_SAMPLE_LIST.search(text):
        declared_local_business = bool(
            re.search(r'["\']fixture_kind["\']\s*:\s*["\']local_business["\']', text)
        )
        path_has_real_hint = bool(_REAL_SAMPLE_HINT.search(relative_path))
        return has_field_pair and (declared_local_business or path_has_real_hint)
    for collection in _EMBEDDED_COLLECTION.finditer(text):
        if not _REAL_SAMPLE_HINT.search(collection.group("name")):
            continue
        following_block = text[collection.start() : collection.start() + 4000]
        if (
            _SAMPLE_PATH_FIELD.search(following_block)
            and _SAMPLE_EXPECTATION_FIELD.search(following_block)
        ) or _SAMPLE_DOCUMENT_ENTRY.search(following_block):
            return True
    return False


def _business_content_category(field: str) -> str:
    normalized = field.casefold()
    if "project" in normalized or "项目" in normalized:
        return "project"
    if "customer" in normalized or "client" in normalized or "客户" in normalized:
        return "customer"
    if any(token in normalized for token in ("owner", "contact", "负责人", "联系人")):
        return "person"
    if any(token in normalized for token in ("invoice", "ticket", "发票", "票号")):
        return "invoice"
    return "source"


def _is_allowed_business_value(value: str) -> bool:
    normalized = value.strip().strip("`*_#-:：.。\"'").casefold()
    if not normalized:
        return True
    if normalized in _BUSINESS_CONTENT_PLACEHOLDERS:
        return True
    if normalized in {
        "str", "string", "optional", "null", "true", "false", "project_manager", "customer", "开户银行", "公司",
        "有限公司", "有限责任公司", "股份有限公司", "集团", "银行", "研究院", "大学",
    }:
        return True
    if re.fullmatch(r"\{[^{}\r\n]+\}", normalized):
        return True
    raw = value.strip()
    if re.fullmatch(r"[\"']{2}[)\]}]*", raw):
        return True
    if re.fullmatch(
        r"\{[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\($",
        raw,
    ):
        return True
    if re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+[\"'`)]*",
        raw.strip("\"'`"),
    ):
        return True
    if re.match(r"[A-Za-z_][A-Za-z0-9_]*\s*[\[(]", raw.strip("\"'`")):
        return True
    return bool(_SYNTHETIC_BUSINESS_VALUE.match(normalized))


def _static_python_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_python_string(node.left)
        right = _static_python_string(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _python_binding_field(target: ast.AST) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Subscript):
        key = _static_python_string(target.slice)
        if key is not None:
            return key
    return None


def _python_literal_business_bindings(node: ast.AST) -> list[tuple[str, str]]:
    if isinstance(node, ast.Assign):
        value = _static_python_string(node.value)
        if value is None:
            return []
        return [
            (field, value)
            for target in node.targets
            if (field := _python_binding_field(target)) is not None
        ]
    if isinstance(node, ast.AnnAssign):
        field = _python_binding_field(node.target)
        value = _static_python_string(node.value) if node.value is not None else None
        if field is None or value is None:
            return []
        return [(field, value)]
    if isinstance(node, ast.Dict):
        bindings: list[tuple[str, str]] = []
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                continue
            field = _static_python_string(key_node)
            value = _static_python_string(value_node)
            if field is not None and value is not None:
                bindings.append((field, value))
        return bindings
    if isinstance(node, ast.Call):
        bindings = []
        for keyword in node.keywords:
            if keyword.arg is None:
                continue
            value = _static_python_string(keyword.value)
            if value is not None:
                bindings.append((keyword.arg, value))
        return bindings
    return []


def _contains_python_literal_business_binding(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        for field, value in _python_literal_business_bindings(node):
            normalized_field = field.casefold()
            if normalized_field in _PYTHON_BUSINESS_FIELDS and not _is_allowed_business_value(value):
                return True
            if normalized_field == "source_kind" and value.strip().casefold() == "real":
                return True
    return False


def _contains_unsanitized_business_content(relative_path: str, text: str) -> bool:
    normalized_path = relative_path.replace("\\", "/")
    if normalized_path.casefold().endswith(".py"):
        if _contains_python_literal_business_binding(text):
            return True
    if (
        _BUSINESS_KB_PATH.match(normalized_path)
        and _SYNTHETIC_DATA_DECLARATION not in text
    ):
        return True

    for match in _STRUCTURED_BUSINESS_VALUE.finditer(text):
        value = (match.group("quoted") or match.group("label_value") or "").strip()
        if _is_allowed_business_value(value):
            continue
        field = (match.group("field") or match.group("label") or "").casefold()
        if field in {"source_file", "source_path", "来源文件", "文件名"}:
            filename = Path(value.replace("\\", "/")).name
            if not re.search(r"\d{4,}", filename) and len(filename) <= 16:
                continue
        return True

    if not normalized_path.casefold().endswith(".py"):
        for match in _UNQUOTED_STRUCTURED_BUSINESS_VALUE.finditer(text):
            if not _is_allowed_business_value(match.group("value")):
                return True

    for pattern in (
        _ORGANIZATION_BUSINESS_VALUE,
        _LABELED_BUSINESS_IDENTIFIER,
        _LABELED_BUSINESS_VALUE,
    ):
        for match in pattern.finditer(text):
            value = match.groupdict().get("value") or match.group(0)
            if not _is_allowed_business_value(value):
                return True

    return bool(_REAL_SOURCE_CLAIM.search(text))


def validate_repository_hygiene(
    project_root: Optional[Path] = None,
    tracked_files: Optional[Iterable[str]] = None,
    verbose: bool = True,
) -> RepositoryHygieneResult:
    """Validate managed tracked text and report the exact scan boundary."""
    root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    paths = list(tracked_files) if tracked_files is not None else tracked_repository_files(root)
    findings: List[dict] = []
    scanned_text_count = 0
    skipped_binary_count = 0
    skipped_non_target_count = 0
    decode_error_count = 0

    for relative_path in paths:
        normalized = relative_path.replace("\\", "/")
        if normalized.startswith("logs/") and normalized.lower().endswith(".json"):
            findings.append(
                _finding(
                    "REPO-TRACKED-RUNTIME-LOG",
                    normalized,
                    "runtime logs/*.json must not be tracked",
                )
            )
            continue

        candidate = root / relative_path
        try:
            payload = candidate.read_bytes()
        except OSError as exc:
            findings.append(
                _finding("REPO-READ-ERROR", normalized, f"tracked file could not be read: {exc}")
            )
            continue

        if not _is_managed_text(normalized):
            suffix = Path(normalized).suffix.casefold()
            if suffix in _SENSITIVE_TRACKED_SUFFIXES:
                findings.append(
                    _finding(
                        "REPO-TRACKED-SENSITIVE-FILE",
                        normalized,
                        "sensitive document, archive, database, key, or media file must not be tracked",
                    )
                )
            elif normalized == _SWIFT_BRIDGE_PATH:
                digest = hashlib.sha256(payload).hexdigest()
                if not payload.startswith(_SWIFT_BRIDGE_HEADER) or digest != _SWIFT_BRIDGE_SHA256:
                    findings.append(
                        _finding(
                            "REPO-TRACKED-BINARY-ALLOWLIST-MISMATCH",
                            normalized,
                            "allowlisted Swift bridge type or digest does not match policy",
                        )
                    )
                else:
                    skipped_binary_count += 1
            elif _non_target_is_binary(payload):
                findings.append(
                    _finding(
                        "REPO-TRACKED-BINARY-FORBIDDEN",
                        normalized,
                        "tracked binary is not in the exact binary allowlist",
                    )
                )
            else:
                findings.append(
                    _finding(
                        "REPO-TRACKED-UNSUPPORTED-FILE",
                        normalized,
                        "tracked file type is not in the managed text allowlist",
                    )
                )
            continue

        try:
            text = _decode_managed_text(payload)
        except (UnicodeDecodeError, _ManagedTextDecodeError):
            decode_error_count += 1
            findings.append(
                _finding(
                    "REPO-TEXT-DECODE",
                    normalized,
                    "managed text must be UTF-8 or BOM-marked UTF-16 without NUL",
                )
            )
            continue
        scanned_text_count += 1
        synthetic_fixture = _is_declared_synthetic_fixture(normalized, text)
        invalid_json = False
        json_credential = False
        if normalized.casefold().endswith(".json"):
            invalid_json, json_credential = _analyze_json_document(text)
            if invalid_json:
                findings.append(
                    _finding(
                        "REPO-INVALID-JSON",
                        normalized,
                        (
                            "tracked JSON must parse strictly and contain unique "
                            "object keys"
                        ),
                    )
                )

        if _contains_rfc1918_url(text):
            findings.append(
                _finding("REPO-RFC1918-URL", normalized, "RFC1918 URL is forbidden")
            )
        if _contains_personal_path(text):
            findings.append(
                _finding(
                    "REPO-PERSONAL-PATH",
                    normalized,
                    "personal absolute path is forbidden",
                )
            )
        if _contains_business_absolute_path(text, synthetic_fixture):
            findings.append(
                _finding(
                    "REPO-BUSINESS-ABSOLUTE-PATH",
                    normalized,
                    "non-user business absolute path is forbidden",
                )
            )
        if json_credential or _contains_hardcoded_credential(text):
            findings.append(
                _finding(
                    "REPO-HARDCODED-CREDENTIAL",
                    normalized,
                    "hardcoded credential or credential-bearing DSN is forbidden",
                )
            )
        if (
            not synthetic_fixture
            and _contains_real_sample_manifest(normalized, text)
        ):
            findings.append(
                _finding(
                    "REPO-REAL-SAMPLE-MANIFEST",
                    normalized,
                    "embedded real-sample manifest is forbidden",
                )
            )

        if not synthetic_fixture and _contains_unsanitized_business_content(normalized, text):
            findings.append(
                _finding(
                    "REPO-REAL-BUSINESS-CONTENT",
                    normalized,
                    "unsanitized structured business content is forbidden",
                )
            )

    report = RepositoryHygieneResult(
        errors=len(findings),
        warnings=0,
        findings=findings,
        tracked_count=len(paths),
        scanned_text_count=scanned_text_count,
        skipped_binary_count=skipped_binary_count,
        skipped_non_target_count=skipped_non_target_count,
        decode_error_count=decode_error_count,
    )
    if verbose:
        for finding in findings:
            print(f"❌ {finding['msg']}")
        print(
            "repository hygiene summary: "
            f"tracked={report.tracked_count}, "
            f"scanned_text={report.scanned_text_count}, "
            f"skipped_binary={report.skipped_binary_count}, "
            f"skipped_non_target={report.skipped_non_target_count}, "
            f"decode_errors={report.decode_error_count}"
        )
        if not findings:
            print("✅ repository hygiene clean")
    return report
