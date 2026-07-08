"""Readiness probes for local, synced, and cloud-placeholder files."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict


def _cloud_blocked_reason(error: BaseException) -> str:
    text = str(error).lower()
    if "cloud operation" in text or "sync" in text or "placeholder" in text:
        return "cloud_placeholder_or_sync_failure"
    if "permission" in text or "access is denied" in text:
        return "permission_denied"
    return "source_not_local_or_unreadable"


def probe_readable_file(path: str, sample_bytes: int = 4096) -> Dict[str, Any]:
    """Return structured readiness for a source file without mutating it."""
    target = Path(path)
    result: Dict[str, Any] = {
        "status": "blocked",
        "path": str(target),
        "exists": target.exists(),
        "is_file": False,
        "readable": False,
        "blocked_reason": "",
        "error": "",
    }
    if not result["exists"]:
        result["blocked_reason"] = "source_missing"
        result["error"] = "File does not exist."
        return result
    if not target.is_file():
        result["blocked_reason"] = "source_not_file"
        result["error"] = "Path exists but is not a file."
        return result

    result["is_file"] = True
    try:
        with open(target, "rb") as f:
            f.read(max(sample_bytes, 0))
    except OSError as e:
        result["blocked_reason"] = _cloud_blocked_reason(e)
        result["error"] = str(e)
        return result

    result["status"] = "ready"
    result["readable"] = True
    return result


def probe_writable_dir(path: str) -> Dict[str, Any]:
    """Return structured readiness for a target directory."""
    target = Path(path)
    result: Dict[str, Any] = {
        "status": "blocked",
        "path": str(target),
        "exists": target.exists(),
        "is_dir": target.is_dir(),
        "writable": False,
        "blocked_reason": "",
        "error": "",
    }
    try:
        target.mkdir(parents=True, exist_ok=True)
        fd, probe_path = tempfile.mkstemp(prefix=".write_probe_", dir=str(target))
        os.close(fd)
        os.unlink(probe_path)
    except OSError as e:
        result["exists"] = target.exists()
        result["is_dir"] = target.is_dir()
        result["blocked_reason"] = _cloud_blocked_reason(e)
        result["error"] = str(e)
        return result

    result.update({
        "status": "ready",
        "exists": True,
        "is_dir": True,
        "writable": True,
    })
    return result
