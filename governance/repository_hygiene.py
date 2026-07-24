"""Repository hygiene checks bounded strictly by Git's tracked-file index."""
from __future__ import annotations

import codecs
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
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
_WINDOWS_BUSINESS_ABSOLUTE_PATH = re.compile(
# Require a root, nested subject, and file so generic examples such as
# C:\\Tools\\PageIndex are not misclassified as business-data locations.
    r"(?<![A-Za-z0-9:/])"
    r"[A-Za-z]:[\\/](?:[^\\/\s\"'`]+[\\/]){2,}[^\\/\s\"'`]+"
)
_POSIX_BUSINESS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])"
    r"/(?:Volumes|mnt|media|srv|volume[0-9]+)/(?:[^/\s\"'`]+/){1,}[^/\s\"'`]+",
    re.IGNORECASE,
)
_CREDENTIAL_DSN = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|amqps?)://"
    r"[^:/\s]+:(?P<secret>[^@/\s]+)@",
    re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?:[\"'])?\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd)\b"
    r"(?:[\"'])?\s*[:=]\s*(?:[rubf]{0,2})?[\"'](?P<secret>[^\"'\r\n]{8,})[\"']",
    re.IGNORECASE,
)
_BEARER_CREDENTIAL = re.compile(
    r"\bAuthorization\s*:\s*Bearer\s+(?P<secret>[A-Za-z0-9._~-]{16,})",
    re.IGNORECASE,
)
_DIRECT_API_CREDENTIAL = re.compile(
    r"\b(?P<secret>(?:sk|pk)-(?:live|prod)-[A-Za-z0-9_-]{16,})\b",
    re.IGNORECASE,
)
_REAL_SAMPLE_HINT = re.compile(
    r"(?:真实样本|real[_ -]?samples?|customer[_ -]?samples?|production[_ -]?samples?)",
    re.IGNORECASE,
)
_EMBEDDED_SAMPLE_MANIFEST = re.compile(
    r"^\s*(?:REAL_?SAMPLES|SAMPLES|SAMPLE_MANIFEST)\s*=\s*[\[\{(]",
    re.MULTILINE,
)
_JSON_SAMPLE_LIST = re.compile(r'[\"\']samples[\"\']\s*:\s*\[', re.IGNORECASE)
_SYNTHETIC_PATH_EXEMPTION = "repo-hygiene: allow=synthetic-path"
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
    wsl_path = re.fullmatch(r"/mnt/([A-Za-z])/(.*)", raw_git_dir)
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


def _is_placeholder_business_path(value: str) -> bool:
    normalized = value.replace("\\", "/").lower()
    return any(
        marker in normalized
        for marker in (
            "${",
            "<",
            ">",
            "...",
            "/project-a/",
            "/synthetic/",
            "/example/",
            "/tmp/",
            "/path/",
            "测试",
            "(",
            ")",
            "[",
            "]",
            "*",
        )
    )


def _contains_business_absolute_path(text: str, synthetic_fixture: bool) -> bool:
    if synthetic_fixture:
        return False
    for line in text.splitlines():
        if _SYNTHETIC_PATH_EXEMPTION in line:
            continue
        for pattern in (
            _WINDOWS_BUSINESS_ABSOLUTE_PATH,
            _POSIX_BUSINESS_ABSOLUTE_PATH,
        ):
            for match in pattern.finditer(line):
                value = match.group(0)
                if _is_placeholder_business_path(value):
                    continue
                if _POSIX_PERSONAL_PATH.search(value) or _WINDOWS_PERSONAL_PATH.search(
                    value
                ):
                    continue
                return True
    return False


_PLACEHOLDER_SECRETS = {
    "secret",
    "secret-pass",
    "password",
    "fake-key-for-test",
    "test-key",
    "dummy-token",
    "redacted",
}


def _is_placeholder_secret(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _PLACEHOLDER_SECRETS:
        return True
    return normalized.startswith(("${", "$ {{", "{{", "<", "%(", "env:"))


def _contains_hardcoded_credential(text: str) -> bool:
    for pattern in (
        _CREDENTIAL_DSN,
        _CREDENTIAL_ASSIGNMENT,
        _BEARER_CREDENTIAL,
        _DIRECT_API_CREDENTIAL,
    ):
        for match in pattern.finditer(text):
            secret = match.groupdict().get("secret", match.group(0))
            if not _is_placeholder_secret(secret):
                return True
    return False


def _contains_real_sample_manifest(relative_path: str, text: str) -> bool:
    evidence = f"{relative_path}\n{text}"
    if not _REAL_SAMPLE_HINT.search(evidence):
        return False
    return bool(
        _EMBEDDED_SAMPLE_MANIFEST.search(text) or _JSON_SAMPLE_LIST.search(text)
    )


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
            if _non_target_is_binary(payload):
                skipped_binary_count += 1
            else:
                skipped_non_target_count += 1
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
        if _contains_hardcoded_credential(text):
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
