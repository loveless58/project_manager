"""Optional local MinerU CLI provider with no implicit installation or download."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable

from ocr.result_schema import normalize_ocr_result


CommandResolver = Callable[[], str | None]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _environment_command() -> str | None:
    value = os.environ.get("PROJECT_MANAGER_MINERU_COMMAND", "").strip()
    return value or None


class MineruProvider:
    """Run only an explicitly configured local MinerU command."""

    name = "mineru"

    def __init__(
        self,
        command: str | None = None,
        *,
        command_resolver: CommandResolver | None = None,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        self._command = command.strip() if isinstance(command, str) and command.strip() else None
        self._command_resolver = command_resolver or _environment_command
        self._runner = runner

    def extract(self, file_path: str) -> dict[str, Any]:
        command = self._command or self._command_resolver()
        if not isinstance(command, str) or not command.strip():
            return self._blocked("ocr_provider_unavailable", "MinerU command is not configured")
        source = Path(file_path)
        if not source.is_file():
            return self._blocked("ocr_input_unavailable", "MinerU input file is unavailable")

        with tempfile.TemporaryDirectory(prefix="project-manager-mineru-") as temporary:
            output_dir = Path(temporary) / "output"
            command_line = [command.strip(), "-p", str(source), "-o", str(output_dir), "-b", "pipeline"]
            try:
                completed = self._runner(
                    command_line,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return self._blocked("ocr_provider_unavailable", str(exc))
            if completed.returncode != 0:
                return self._blocked("ocr_provider_failed", "MinerU command failed")
            text = _read_mineru_text(output_dir)
            if not text:
                return self._blocked("ocr_empty_text", "MinerU produced no readable structured text")
            return normalize_ocr_result(
                {
                    "status": "success",
                    "engine": self.name,
                    "text": text,
                    "pages": [],
                }
            )

    def _blocked(self, reason: str, error: str) -> dict[str, Any]:
        return normalize_ocr_result(
            {
                "status": "blocked",
                "engine": self.name,
                "blocked_reason": reason,
                "error": error,
            }
        )


def _read_mineru_text(output_dir: Path) -> str:
    if not output_dir.is_dir():
        return ""
    for path in sorted(output_dir.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    for path in sorted(output_dir.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        text = _first_text(payload)
        if text:
            return text
    return ""


def _first_text(value: object) -> str:
    if isinstance(value, dict):
        for key in ("markdown", "text", "content"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            found = _first_text(item)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = _first_text(item)
            if found:
                return found
    return ""


__all__ = ["MineruProvider"]
