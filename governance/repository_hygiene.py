"""Repository hygiene checks bounded strictly by Git's tracked-file index."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, List, Mapping, Optional, Tuple


_RFC1918_URL = re.compile(
    r"https?://(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?::\d+)?(?:[/\w.?=&%+#~-]*)?",
    re.IGNORECASE,
)
_POSIX_PERSONAL_PATH = re.compile(
    r"/(?:Users|home)/[^/\s\"']+(?:/[^\s\"']*)?"
)
_WINDOWS_PERSONAL_PATH = re.compile(
    r"\b[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"']+(?:[\\/][^\s\"']*)?",
    re.IGNORECASE,
)


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


def validate_repository_hygiene(
    project_root: Optional[Path] = None,
    tracked_files: Optional[Iterable[str]] = None,
    verbose: bool = True,
) -> Tuple[int, int, List[dict]]:
    """Reject private endpoints, personal paths and tracked runtime traces.

    Only files returned by ``git ls-files`` are considered. Binary tracked files
    are skipped after a NUL-byte or UTF-8 decode check, so generated and ignored
    workspace content cannot leak into the scan boundary.
    """
    root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    paths = list(tracked_files) if tracked_files is not None else tracked_repository_files(root)
    findings: List[dict] = []

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
        if b"\0" in payload:
            continue
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            continue

        if _RFC1918_URL.search(text):
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

    if verbose:
        for finding in findings:
            print(f"❌ {finding['msg']}")
        if not findings:
            print(f"✅ repository hygiene clean: {len(paths)} tracked files scanned")
    return len(findings), 0, findings
