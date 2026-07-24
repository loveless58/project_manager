"""Client for the optional external PageIndex CLI.

The client separates configuration from capability validation and exposes only
stable, sanitized failures at its public boundary.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from collections.abc import Mapping
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


_ERROR_MESSAGES = {
    "PAGEINDEX.CONFIG.MISSING": (
        "pageindex_dir must be configured explicitly through AppSettings"
    ),
    "PAGEINDEX.INPUT.PDF_NOT_FOUND": "PDF file not found.",
    "PAGEINDEX.INPUT.MARKDOWN_EXTENSION": (
        "Markdown file must use a .md or .markdown extension."
    ),
    "PAGEINDEX.INPUT.MARKDOWN_NOT_FOUND": "Markdown file not found.",
    "PAGEINDEX.INPUT.READ_FAILED": "PageIndex input could not be staged.",
    "PAGEINDEX.INPUT.HASH_INVALID": (
        "PageIndex content hash must be a SHA-256 digest."
    ),
    "PAGEINDEX.INPUT.HASH_MISMATCH": (
        "PageIndex input does not match the requested content hash."
    ),
    "PAGEINDEX.INPUT.IDENTITY_MISMATCH": (
        "PageIndex operation identity does not match the staged input."
    ),
    "PAGEINDEX.RUNTIME.WORKSPACE_PATH_TOO_LONG": (
        "PageIndex runtime workspace path is too long."
    ),
    "PAGEINDEX.RUNTIME.VERSION_CHANGED": (
        "PageIndex provider changed during the indexing operation."
    ),
    "PAGEINDEX.RUNTIME.VERSION_UNAVAILABLE": (
        "PageIndex provider version is unavailable."
    ),
    "PAGEINDEX.RUNTIME.PYTHON_UNAVAILABLE": (
        "PageIndex Python interpreter is unavailable."
    ),
    "PAGEINDEX.RUNTIME.CLI_UNAVAILABLE": "PageIndex CLI script is unavailable.",
    "PAGEINDEX.RUNTIME.PROBE_TIMEOUT": "PageIndex runtime probe timed out.",
    "PAGEINDEX.RUNTIME.PROBE_START_FAILED": (
        "PageIndex runtime probe could not be started."
    ),
    "PAGEINDEX.RUNTIME.PROBE_FAILED": "PageIndex runtime probe failed.",
    "PAGEINDEX.RUNTIME.UNAVAILABLE": "PageIndex runtime is unavailable.",
    "PAGEINDEX.EXECUTION.TIMEOUT": "PageIndex execution timed out.",
    "PAGEINDEX.EXECUTION.START_FAILED": (
        "PageIndex execution could not be started."
    ),
    "PAGEINDEX.EXECUTION.FAILED": "PageIndex execution failed.",
    "PAGEINDEX.RESULT.MISSING": "PageIndex structure result was not generated.",
    "PAGEINDEX.RESULT.DECODE_FAILED": (
        "PageIndex structure result could not be decoded."
    ),
    "PAGEINDEX.RESULT.READ_FAILED": "PageIndex structure result could not be read.",
    "PAGEINDEX.RESULT.INVALID_JSON": "PageIndex structure result is invalid.",
    "PAGEINDEX.RESULT.INVALID_SCHEMA": (
        "PageIndex structure result has an invalid schema."
    ),
    "PAGEINDEX.RESULT.PERSIST_FAILED": (
        "PageIndex structure result could not be persisted."
    ),
    "PAGEINDEX.CONTENT.PDF_NOT_FOUND": "PDF file not found.",
    "PAGEINDEX.CONTENT.READ_FAILED": "PDF content could not be read.",
}


def build_operation_identity(
    document_version_id: str,
    content_hash: str,
    provider_version: str,
) -> str:
    """Build a deterministic, opaque identity for one index artifact."""
    payload = json.dumps(
        {
            "content_hash": str(content_hash),
            "document_version_id": str(document_version_id),
            "provider_version": str(provider_version),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def normalize_content_hash(value: Optional[str]) -> Optional[str]:
    """Return a canonical SHA-256 digest, or None for an invalid value."""
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized.split(":", 1)[1]
    if re.fullmatch(r"[0-9a-f]{64}", normalized):
        return normalized
    return None


class _OperationFailure(Exception):
    """Internal control-flow error containing only a stable public code."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class PageIndexError(Exception):
    """Stable error raised when a local PageIndex helper cannot proceed."""

    def __init__(
        self,
        message: str,
        error_code: str = "PAGEINDEX.RUNTIME.UNAVAILABLE",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code


class PageIndexClient:
    """PageIndex adapter with pure-Python retrieval and traversal helpers."""

    runtime_probe_timeout_seconds = 5

    def __init__(
        self,
        pageindex_dir: Optional[str] = None,
        timeout_seconds: int = 600,
        workspace_root: Optional[str] = None,
    ) -> None:
        if not pageindex_dir:
            code = "PAGEINDEX.CONFIG.MISSING"
            raise PageIndexError(_ERROR_MESSAGES[code], code)
        self.pageindex_dir = str(Path(pageindex_dir).expanduser().resolve())
        venv_bin = "Scripts" if os.name == "nt" else "bin"
        python_name = "python.exe" if os.name == "nt" else "python"
        self.python_bin = os.path.join(
            self.pageindex_dir, ".venv", venv_bin, python_name
        )
        self.cli_script = os.path.join(self.pageindex_dir, "run_pageindex.py")
        self.timeout_seconds = timeout_seconds
        default_workspace = (
            Path(tempfile.gettempdir()) / "project-manager" / "pageindex"
        )
        self.workspace_root = str(
            Path(workspace_root or default_workspace).expanduser().resolve()
        )

    @property
    def provider_version(self) -> str:
        """Return a manifest fingerprint for provider code and configuration."""
        try:
            digest = self._provider_manifest_digest()
        except (OSError, UnicodeError):
            return ""
        return f"pageindex-provider-v2:{digest}"

    def _provider_manifest_digest(self) -> str:
        root = Path(self.pageindex_dir)
        if not root.is_dir():
            raise OSError("provider root is unavailable")
        excluded_directories = {
            ".git",
            ".venv",
            "__pycache__",
            "artifacts",
            "results",
            "staging",
        }
        included_suffixes = {
            ".json",
            ".lock",
            ".py",
            ".toml",
            ".yaml",
            ".yml",
        }
        included_names = {
            "requirements.txt",
        }
        candidates: List[Path] = []
        for current_root, directory_names, file_names in os.walk(root):
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name.lower() not in excluded_directories
            )
            directory = Path(current_root)
            for file_name in sorted(file_names):
                path = directory / file_name
                if (
                    path.suffix.lower() in included_suffixes
                    or file_name.lower() in included_names
                ):
                    candidates.append(path)
        if not candidates:
            raise OSError("provider manifest is empty")

        digest = hashlib.sha256(b"pageindex-provider-manifest-v2\0")
        for path in sorted(
            candidates,
            key=lambda candidate: candidate.relative_to(root).as_posix(),
        ):
            relative_path = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(relative_path)
            digest.update(b"\0")
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            digest.update(b"\0")
        return digest.hexdigest()

    def check_environment(self) -> None:
        """Validate that the external PageIndex runtime can start safely."""
        if not os.path.isfile(self.python_bin):
            code = "PAGEINDEX.RUNTIME.PYTHON_UNAVAILABLE"
            raise PageIndexError(_ERROR_MESSAGES[code], code)
        if not os.path.isfile(self.cli_script):
            code = "PAGEINDEX.RUNTIME.CLI_UNAVAILABLE"
            raise PageIndexError(_ERROR_MESSAGES[code], code)
        try:
            process = subprocess.run(
                [self.python_bin, "--version"],
                cwd=self.pageindex_dir,
                capture_output=True,
                text=True,
                timeout=min(self.timeout_seconds, self.runtime_probe_timeout_seconds),
            )
        except subprocess.TimeoutExpired:
            code = "PAGEINDEX.RUNTIME.PROBE_TIMEOUT"
            raise PageIndexError(_ERROR_MESSAGES[code], code) from None
        except (OSError, UnicodeError):
            code = "PAGEINDEX.RUNTIME.PROBE_START_FAILED"
            raise PageIndexError(_ERROR_MESSAGES[code], code) from None
        if process.returncode != 0:
            code = "PAGEINDEX.RUNTIME.PROBE_FAILED"
            raise PageIndexError(_ERROR_MESSAGES[code], code)

    def index_pdf(
        self,
        pdf_path: str,
        model: Optional[str] = None,
        toc_check_pages: Optional[int] = None,
        max_pages_per_node: Optional[int] = None,
        max_tokens_per_node: Optional[int] = None,
        if_add_node_id: str = "yes",
        if_add_node_summary: str = "yes",
        if_add_doc_description: str = "yes",
        if_add_node_text: str = "yes",
        if_thinning: str = "no",
        thinning_threshold: int = 5000,
        summary_token_threshold: int = 200,
        operation_id: Optional[str] = None,
        document_version_id: str = "",
        expected_content_hash: Optional[str] = None,
        expected_provider_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Index a PDF through the external PageIndex CLI."""
        if not os.path.isfile(pdf_path):
            return self._failed("PAGEINDEX.INPUT.PDF_NOT_FOUND")
        command = [self.python_bin, self.cli_script, "--pdf_path", pdf_path]
        options = {
            "model": model,
            "toc_check_pages": toc_check_pages,
            "max_pages_per_node": max_pages_per_node,
            "max_tokens_per_node": max_tokens_per_node,
            "if_add_node_id": if_add_node_id,
            "if_add_node_summary": if_add_node_summary,
            "if_add_doc_description": if_add_doc_description,
            "if_add_node_text": if_add_node_text,
            "if_thinning": if_thinning,
            "thinning_threshold": thinning_threshold,
            "summary_token_threshold": summary_token_threshold,
        }
        self._append_options(command, options)
        return self._run_index(
            command,
            pdf_path,
            operation_id=operation_id,
            document_version_id=document_version_id,
            expected_content_hash=expected_content_hash,
            expected_provider_version=expected_provider_version,
        )

    def index_md(
        self,
        md_path: str,
        *,
        operation_id: Optional[str] = None,
        document_version_id: str = "",
        expected_content_hash: Optional[str] = None,
        expected_provider_version: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Index a Markdown document through the external PageIndex CLI."""
        if not md_path.lower().endswith((".md", ".markdown")):
            return self._failed("PAGEINDEX.INPUT.MARKDOWN_EXTENSION")
        if not os.path.isfile(md_path):
            return self._failed("PAGEINDEX.INPUT.MARKDOWN_NOT_FOUND")
        command = [self.python_bin, self.cli_script, "--md_path", md_path]
        supported = {
            "if_add_node_id",
            "if_add_node_summary",
            "if_add_doc_description",
            "if_add_node_text",
            "if_thinning",
            "thinning_threshold",
            "summary_token_threshold",
            "model",
        }
        self._append_options(
            command, {key: value for key, value in kwargs.items() if key in supported}
        )
        return self._run_index(
            command,
            md_path,
            operation_id=operation_id,
            document_version_id=document_version_id,
            expected_content_hash=expected_content_hash,
            expected_provider_version=expected_provider_version,
        )

    def _environment_failure(self) -> Optional[Dict[str, Any]]:
        try:
            self.check_environment()
        except PageIndexError as exc:
            code = (
                exc.error_code
                if exc.error_code in _ERROR_MESSAGES
                else "PAGEINDEX.RUNTIME.UNAVAILABLE"
            )
            return self._failed(code)
        return None

    @staticmethod
    def _append_options(command: List[str], options: Dict[str, Any]) -> None:
        for key, value in options.items():
            if value is not None:
                command.extend([f"--{key.replace('_', '-')}", str(value)])

    @staticmethod
    def _failed(error_code: str, elapsed_seconds: float = 0.0) -> Dict[str, Any]:
        return {
            "status": "failed",
            "engine": "pageindex",
            "error_code": error_code,
            "error": _ERROR_MESSAGES.get(
                error_code,
                "PageIndex provider failed.",
            ),
            "elapsed_seconds": elapsed_seconds,
        }

    def _run_index(
        self,
        command: List[str],
        source_path: str,
        *,
        operation_id: Optional[str],
        document_version_id: str,
        expected_content_hash: Optional[str],
        expected_provider_version: Optional[str],
    ) -> Dict[str, Any]:
        start = time.time()
        attempt_dir: Optional[Path] = None
        pinned_provider_version = self.provider_version
        if not pinned_provider_version:
            return self._failed("PAGEINDEX.RUNTIME.VERSION_UNAVAILABLE")
        if (
            expected_provider_version is not None
            and str(expected_provider_version).strip() != pinned_provider_version
        ):
            return self._failed("PAGEINDEX.RUNTIME.VERSION_CHANGED")
        canonical_document_version_id = (
            str(document_version_id).strip() or "standalone"
        )
        staged_suffix = ".pdf" if "--pdf_path" in command else ".md"

        try:
            try:
                (
                    attempt_dir,
                    staged_source,
                    source_sha256,
                    resolved_operation_id,
                ) = self._stage_operation(
                    source_path=Path(source_path),
                    staged_suffix=staged_suffix,
                    operation_id=operation_id,
                    document_version_id=canonical_document_version_id,
                    expected_content_hash=expected_content_hash,
                    provider_version=pinned_provider_version,
                )
            except _OperationFailure as exc:
                return self._failed(
                    exc.error_code,
                    round(time.time() - start, 2),
                )

            environment_failure = self._environment_failure()
            if environment_failure is not None:
                return environment_failure
            current_provider_version = self.provider_version
            if not current_provider_version:
                return self._failed(
                    "PAGEINDEX.RUNTIME.VERSION_UNAVAILABLE",
                    round(time.time() - start, 2),
                )
            if current_provider_version != pinned_provider_version:
                return self._failed(
                    "PAGEINDEX.RUNTIME.VERSION_CHANGED",
                    round(time.time() - start, 2),
                )

            staged_command = list(command)
            for input_flag in ("--pdf_path", "--md_path"):
                if input_flag in staged_command:
                    staged_command[staged_command.index(input_flag) + 1] = str(
                        staged_source
                    )
                    break

            try:
                process = subprocess.run(
                    staged_command,
                    cwd=str(attempt_dir),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                return self._failed(
                    "PAGEINDEX.EXECUTION.TIMEOUT",
                    round(time.time() - start, 2),
                )
            except (OSError, UnicodeError):
                return self._failed(
                    "PAGEINDEX.EXECUTION.START_FAILED",
                    round(time.time() - start, 2),
                )

            elapsed = round(time.time() - start, 2)
            current_provider_version = self.provider_version
            if not current_provider_version:
                return self._failed(
                    "PAGEINDEX.RUNTIME.VERSION_UNAVAILABLE",
                    elapsed,
                )
            if current_provider_version != pinned_provider_version:
                return self._failed("PAGEINDEX.RUNTIME.VERSION_CHANGED", elapsed)
            if process.returncode != 0:
                return self._failed("PAGEINDEX.EXECUTION.FAILED", elapsed)

            structure_path = (
                attempt_dir
                / "results"
                / f"{staged_source.stem}_structure.json"
            )
            if not structure_path.is_file():
                return self._failed("PAGEINDEX.RESULT.MISSING", elapsed)
            try:
                with open(structure_path, "r", encoding="utf-8") as source:
                    data = json.load(source)
            except UnicodeError:
                return self._failed("PAGEINDEX.RESULT.DECODE_FAILED", elapsed)
            except OSError:
                return self._failed("PAGEINDEX.RESULT.READ_FAILED", elapsed)
            except json.JSONDecodeError:
                return self._failed("PAGEINDEX.RESULT.INVALID_JSON", elapsed)

            if not self._has_valid_result_schema(data):
                return self._failed("PAGEINDEX.RESULT.INVALID_SCHEMA", elapsed)

            try:
                artifact_path, source_artifact_path = self._persist_artifact(
                    operation_id=resolved_operation_id,
                    document_version_id=canonical_document_version_id,
                    content_hash=source_sha256,
                    source_sha256=source_sha256,
                    provider_version=pinned_provider_version,
                    doc_name=os.path.basename(source_path),
                    staged_source=staged_source,
                    structure=data.get("structure", []),
                )
            except _OperationFailure as exc:
                return self._failed(exc.error_code, elapsed)

            result = {
                "status": "success",
                "engine": "pageindex",
                "doc_name": os.path.basename(source_path),
                "doc_id": resolved_operation_id,
                "operation_id": resolved_operation_id,
                "provider_version": pinned_provider_version,
                "content_hash": source_sha256,
                "source_sha256": source_sha256,
                "structure": data.get("structure", []),
                "structure_json_path": artifact_path,
                "elapsed_seconds": elapsed,
            }
            if source_artifact_path:
                result["source_artifact_path"] = source_artifact_path
                result["source_artifact_name"] = Path(source_artifact_path).name
            return result
        finally:
            if attempt_dir is not None:
                shutil.rmtree(attempt_dir, ignore_errors=True)

    def _stage_operation(
        self,
        *,
        source_path: Path,
        staged_suffix: str,
        operation_id: Optional[str],
        document_version_id: str,
        expected_content_hash: Optional[str],
        provider_version: str,
    ) -> tuple[Path, Path, str, str]:
        attempt_dir = (
            Path(self.workspace_root)
            / "staging"
            / uuid.uuid4().hex[:12]
        )
        staged_source = attempt_dir / f"input{staged_suffix}"
        predicted_result = (
            attempt_dir
            / "results"
            / f"{staged_source.stem}_structure.json"
        )
        if self._windows_path_too_long(predicted_result):
            raise _OperationFailure(
                "PAGEINDEX.RUNTIME.WORKSPACE_PATH_TOO_LONG"
            )
        try:
            attempt_dir.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(source_path, staged_source)
            source_sha256 = self._sha256_file(staged_source)
            normalized_expected = normalize_content_hash(expected_content_hash)
            if (
                expected_content_hash is not None
                and normalized_expected is None
            ):
                raise _OperationFailure("PAGEINDEX.INPUT.HASH_INVALID")
            if (
                normalized_expected is not None
                and normalized_expected != source_sha256
            ):
                raise _OperationFailure("PAGEINDEX.INPUT.HASH_MISMATCH")

            authoritative_operation_id = build_operation_identity(
                document_version_id,
                source_sha256,
                provider_version,
            )
            supplied_operation_id = str(operation_id or "").strip().lower()
            if (
                supplied_operation_id
                and supplied_operation_id != authoritative_operation_id
            ):
                raise _OperationFailure(
                    "PAGEINDEX.INPUT.IDENTITY_MISMATCH"
                )
            return (
                attempt_dir,
                staged_source,
                source_sha256,
                authoritative_operation_id,
            )
        except _OperationFailure:
            shutil.rmtree(attempt_dir, ignore_errors=True)
            raise
        except (OSError, UnicodeError):
            shutil.rmtree(attempt_dir, ignore_errors=True)
            raise _OperationFailure("PAGEINDEX.INPUT.READ_FAILED") from None

    @staticmethod
    def _windows_path_too_long(path: Path) -> bool:
        return os.name == "nt" and len(os.path.abspath(str(path))) >= 240

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _persist_artifact(
        self,
        *,
        operation_id: str,
        document_version_id: str,
        content_hash: str,
        source_sha256: str,
        provider_version: str,
        doc_name: str,
        staged_source: Path,
        structure: List[Any],
    ) -> tuple[str, str]:
        artifact_root = Path(self.workspace_root) / "artifacts"
        target = artifact_root / f"{operation_id}.json"
        temporary = artifact_root / f".{uuid.uuid4().hex}.json.tmp"
        source_target: Optional[Path] = None
        source_temporary: Optional[Path] = None
        published_source_identity: Optional[tuple[int, int, int, int]] = None
        if staged_source.suffix.lower() == ".pdf":
            source_publication_id = uuid.uuid4().hex
            source_target = artifact_root / f"source-{source_publication_id}.pdf"
            source_temporary = (
                artifact_root / f".source-{source_publication_id}.pdf.tmp"
            )
        paths_to_check = [target, temporary]
        if source_target is not None and source_temporary is not None:
            paths_to_check.extend([source_target, source_temporary])
        if any(self._windows_path_too_long(path) for path in paths_to_check):
            raise _OperationFailure("PAGEINDEX.RESULT.PERSIST_FAILED")

        envelope = {
            "schema_version": "pageindex_artifact.v2",
            "operation_id": operation_id,
            "document_version_id": document_version_id,
            "content_hash": content_hash,
            "source_sha256": source_sha256,
            "provider": "pageindex",
            "provider_version": provider_version,
            "doc_name": doc_name,
            "source_artifact_name": (
                source_target.name if source_target is not None else None
            ),
            "structure": structure,
        }
        try:
            artifact_root.mkdir(parents=True, exist_ok=True)
            if source_target is not None and source_temporary is not None:
                with staged_source.open("rb") as source:
                    with source_temporary.open("xb") as destination:
                        shutil.copyfileobj(source, destination)
                        destination.flush()
                        os.fsync(destination.fileno())
                published_source_identity = self._file_identity(
                    source_temporary
                )
                os.replace(source_temporary, source_target)

            with temporary.open("x", encoding="utf-8") as destination:
                json.dump(
                    envelope,
                    destination,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                destination.flush()
                os.fsync(destination.fileno())
            os.replace(temporary, target)
        except (OSError, TypeError, ValueError):
            if (
                source_target is not None
                and published_source_identity is not None
                and not self._artifact_references_source(
                    target, source_target.name
                )
                and self._file_identity(source_target)
                == published_source_identity
            ):
                try:
                    source_target.unlink()
                except OSError:
                    pass
            raise _OperationFailure("PAGEINDEX.RESULT.PERSIST_FAILED") from None
        finally:
            for temporary_path in (temporary, source_temporary):
                if temporary_path is None:
                    continue
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return (
            str(target),
            str(source_target) if source_target is not None else "",
        )

    @staticmethod
    def _file_identity(path: Path) -> Optional[tuple[int, int, int, int]]:
        """Return a replacement-sensitive identity for owned artifact cleanup."""
        try:
            stat_result = path.stat()
        except OSError:
            return None
        return (
            int(stat_result.st_dev),
            int(stat_result.st_ino),
            int(stat_result.st_size),
            int(stat_result.st_ctime_ns),
        )

    @staticmethod
    def _artifact_references_source(
        artifact_path: Path,
        source_artifact_name: str,
    ) -> bool:
        """Return whether the current JSON commit marker owns a snapshot."""
        try:
            with artifact_path.open("r", encoding="utf-8") as source:
                payload = json.load(source)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ):
            return False
        return isinstance(payload, Mapping) and (
            payload.get("source_artifact_name") == source_artifact_name
        )

    def collect_orphaned_source_artifacts(
        self,
        *,
        min_age_seconds: Optional[float] = None,
    ) -> int:
        """Collect old unreferenced snapshots at an explicit maintenance boundary.

        Indexing never invokes this collector synchronously.  The default grace
        period is longer than an indexing request, so an in-flight attempt's
        unique snapshot is not eligible.  Only managed ``source-<uuid>.pdf``
        files absent from every current JSON commit marker may be removed.
        """
        grace_seconds = (
            max(float(self.timeout_seconds) * 2.0, 3600.0)
            if min_age_seconds is None
            else float(min_age_seconds)
        )
        if grace_seconds < 0 or not math.isfinite(grace_seconds):
            raise ValueError("min_age_seconds must be a finite non-negative value")

        artifact_root = Path(self.workspace_root) / "artifacts"
        if not artifact_root.is_dir():
            return 0
        cutoff = time.time() - grace_seconds
        removed = 0
        for candidate in artifact_root.glob("source-*.pdf"):
            if not re.fullmatch(r"source-[0-9a-f]{32}\.pdf", candidate.name):
                continue
            try:
                if candidate.stat().st_mtime > cutoff:
                    continue
            except OSError:
                continue
            if self._source_artifact_is_referenced(
                artifact_root,
                candidate.name,
            ):
                continue
            candidate_identity = self._file_identity(candidate)
            if candidate_identity is None:
                continue
            if self._file_identity(candidate) != candidate_identity:
                continue
            if self._source_artifact_is_referenced(
                artifact_root,
                candidate.name,
            ):
                continue
            try:
                candidate.unlink()
            except OSError:
                continue
            removed += 1
        return removed

    def _source_artifact_is_referenced(
        self,
        artifact_root: Path,
        source_artifact_name: str,
    ) -> bool:
        return any(
            self._artifact_references_source(path, source_artifact_name)
            for path in artifact_root.glob("*.json")
        )

    @classmethod
    def _has_valid_result_schema(cls, data: Any) -> bool:
        """Validate only the JSON fields consumed by the public client result."""
        if not isinstance(data, Mapping):
            return False
        doc_name = data.get("doc_name")
        if doc_name is not None and not isinstance(doc_name, str):
            return False
        structure = data.get("structure", [])
        return isinstance(structure, list) and cls._has_valid_nodes(structure)

    @classmethod
    def _has_valid_nodes(cls, nodes: List[Any]) -> bool:
        for node in nodes:
            if not isinstance(node, Mapping):
                return False
            if "nodes" in node:
                children = node["nodes"]
                if not isinstance(children, list) or not cls._has_valid_nodes(children):
                    return False
        return True

    def get_page_content(self, pdf_path: str, pages: str) -> List[Dict[str, Any]]:
        """Read PDF text without requiring a PageIndex CLI environment."""
        if not os.path.isfile(pdf_path):
            code = "PAGEINDEX.CONTENT.PDF_NOT_FOUND"
            raise PageIndexError(_ERROR_MESSAGES[code], code)
        page_numbers = self._parse_pages(pages)
        try:
            return self._get_pdf_page_content_pypdf2(pdf_path, page_numbers)
        except Exception:
            try:
                return self._get_pdf_page_content_pymupdf(pdf_path, page_numbers)
            except Exception:
                code = "PAGEINDEX.CONTENT.READ_FAILED"
                raise PageIndexError(_ERROR_MESSAGES[code], code) from None

    def get_page_content_from_index(
        self, index_result: Dict[str, Any], pages: str
    ) -> List[Dict[str, Any]]:
        source_artifact_path = index_result.get("source_artifact_path")
        if (
            isinstance(source_artifact_path, str)
            and os.path.isfile(source_artifact_path)
        ):
            return self.get_page_content(source_artifact_path, pages)

        source_artifact_name = index_result.get("source_artifact_name")
        structure_json_path = index_result["structure_json_path"]
        if (
            isinstance(source_artifact_name, str)
            and Path(source_artifact_name).name == source_artifact_name
        ):
            named_source_artifact = str(
                Path(structure_json_path).parent / source_artifact_name
            )
            if os.path.isfile(named_source_artifact):
                return self.get_page_content(named_source_artifact, pages)

        content_addressed_pdf = str(
            Path(structure_json_path).with_suffix(".pdf")
        )
        if os.path.isfile(content_addressed_pdf):
            return self.get_page_content(content_addressed_pdf, pages)

        legacy_pdf_path = structure_json_path.replace(
            "_structure.json",
            ".pdf",
        )
        if not os.path.isfile(legacy_pdf_path):
            legacy_pdf_path = os.path.join(
                os.path.dirname(structure_json_path),
                f"{index_result['doc_name']}.pdf",
            )
        return self.get_page_content(legacy_pdf_path, pages)

    @staticmethod
    def find_nodes_by_title(
        index_result: Dict[str, Any], keyword: str
    ) -> List[Dict[str, Any]]:
        """Traverse an existing structure without creating or probing a client."""
        pattern = re.compile(keyword, re.IGNORECASE)
        matches: List[Dict[str, Any]] = []

        def traverse(nodes: List[Dict[str, Any]]) -> None:
            for node in nodes:
                if pattern.search(node.get("title", "")):
                    matches.append(
                        {
                            "title": node.get("title"),
                            "node_id": node.get("node_id"),
                            "start_index": node.get("start_index"),
                            "end_index": node.get("end_index"),
                            "summary": node.get("summary"),
                        }
                    )
                children = node.get("nodes")
                if children:
                    traverse(children)

        traverse(index_result.get("structure", []))
        return matches

    @staticmethod
    def _parse_pages(pages: str) -> List[int]:
        result: List[int] = []
        for part in pages.split(","):
            part = part.strip()
            if "-" in part:
                start_text, end_text = part.split("-", 1)
                start, end = int(start_text.strip()), int(end_text.strip())
                if start > end:
                    raise ValueError("Invalid page range: start must be <= end")
                result.extend(range(start, end + 1))
            else:
                result.append(int(part))
        return sorted(set(result))

    @staticmethod
    def _get_pdf_page_content_pypdf2(
        pdf_path: str, page_numbers: List[int]
    ) -> List[Dict[str, Any]]:
        import PyPDF2

        with open(pdf_path, "rb") as source:
            reader = PyPDF2.PdfReader(source)
            total = len(reader.pages)
            valid_pages = [page for page in page_numbers if 1 <= page <= total]
            return [
                {"page": page, "content": reader.pages[page - 1].extract_text() or ""}
                for page in valid_pages
            ]

    @staticmethod
    def _get_pdf_page_content_pymupdf(
        pdf_path: str, page_numbers: List[int]
    ) -> List[Dict[str, Any]]:
        import fitz

        document = fitz.open(pdf_path)
        try:
            valid_pages = [
                page for page in page_numbers if 1 <= page <= document.page_count
            ]
            return [
                {"page": page, "content": document.load_page(page - 1).get_text() or ""}
                for page in valid_pages
            ]
        finally:
            document.close()
