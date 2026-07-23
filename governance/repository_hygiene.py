"""Repository hygiene checks bounded strictly by Git's tracked-file index."""
from __future__ import annotations

import codecs
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, Iterator, List, Mapping, Optional
from urllib.parse import urlsplit


_HTTP_URL = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
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
    if payload.startswith(codecs.BOM_UTF8):
        return payload.decode("utf-8-sig")
    if payload.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return payload.decode("utf-16")
    return payload.decode("utf-8")


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
        except UnicodeDecodeError:
            decode_error_count += 1
            findings.append(
                _finding(
                    "REPO-TEXT-DECODE",
                    normalized,
                    "managed tracked text must be UTF-8 or BOM-marked UTF-16",
                )
            )
            continue
        scanned_text_count += 1

        if _contains_rfc1918_url(text):
            findings.append(
                _finding("REPO-RFC1918-URL", normalized, "RFC1918 URL is forbidden")
            )
        if _POSIX_PERSONAL_PATH.search(text) or _WINDOWS_PERSONAL_PATH.search(text):
            findings.append(
                _finding(
                    "REPO-PERSONAL-PATH",
                    normalized,
                    "personal absolute path is forbidden",
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
