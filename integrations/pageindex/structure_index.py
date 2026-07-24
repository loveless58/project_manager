"""StructureIndex port adapter backed by the optional PageIndex CLI."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Callable, Optional, Union

from platform_core.models import CapabilityReport, StructureIndexRequest, StructureIndexResult

from .pageindex_client import (
    PageIndexClient,
    PageIndexError,
    build_operation_identity,
    normalize_content_hash,
)


ClientFactory = Callable[[str], PageIndexClient]
_PROVIDER_UNAVAILABLE = "PageIndex provider is unavailable."
_PROVIDER_FAILED = "PageIndex provider failed."
_UNAVAILABLE_RESULT_CODES = {"PAGEINDEX.CONFIG.MISSING"}
_UNAVAILABLE_RESULT_PREFIX = "PAGEINDEX.RUNTIME."


def _provider_version(client: PageIndexClient) -> str:
    value = getattr(client, "provider_version", "")
    return "" if value is None else str(value).strip()


def _is_provider_unavailable_result(raw: object) -> bool:
    if not isinstance(raw, Mapping):
        return False
    error_code = raw.get("error_code")
    return isinstance(error_code, str) and (
        error_code in _UNAVAILABLE_RESULT_CODES
        or error_code.startswith(_UNAVAILABLE_RESULT_PREFIX)
    )


class PageIndexStructureIndex:
    """Map sanitized PageIndex results onto the StructureIndex port."""

    name = "pageindex"

    def __init__(
        self,
        pageindex_dir: Union[str, Path],
        client_factory: Optional[ClientFactory] = None,
        workspace_root: Optional[Union[str, Path]] = None,
    ) -> None:
        self.pageindex_dir = str(Path(pageindex_dir).expanduser().resolve())
        self.client_factory = client_factory
        self.workspace_root = (
            str(Path(workspace_root).expanduser().resolve())
            if workspace_root is not None
            else None
        )

    def _client(self) -> PageIndexClient:
        if self.client_factory is not None:
            return self.client_factory(self.pageindex_dir)
        return PageIndexClient(
            self.pageindex_dir,
            workspace_root=self.workspace_root,
        )

    @property
    def provider_version(self) -> str:
        client = self._client()
        return _provider_version(client)

    def probe(self) -> CapabilityReport:
        try:
            client = self._client()
            provider_version = _provider_version(client)
            if not provider_version:
                return CapabilityReport(
                    "blocked",
                    self.name,
                    "",
                    _PROVIDER_UNAVAILABLE,
                )
            client.check_environment()
            if _provider_version(client) != provider_version:
                return CapabilityReport(
                    "blocked",
                    self.name,
                    "",
                    _PROVIDER_UNAVAILABLE,
                )
        except PageIndexError:
            return CapabilityReport("blocked", self.name, "", _PROVIDER_UNAVAILABLE)
        return CapabilityReport("ready", self.name, provider_version, "ready")

    def index(self, request: StructureIndexRequest) -> StructureIndexResult:
        if request.media_type not in {"application/pdf", "text/markdown"}:
            return StructureIndexResult(
                "blocked",
                self.name,
                "",
                (),
                "INDEX.UNSUPPORTED_MEDIA_TYPE",
                "Unsupported media type.",
            )

        document_version_id = str(request.document_version_id).strip()
        content_hash = normalize_content_hash(request.content_hash)
        if not document_version_id:
            return StructureIndexResult(
                "failed",
                self.name,
                "",
                (),
                "INDEX.INPUT_IDENTITY_INVALID",
                "Structure-index document version identity is required.",
            )
        if content_hash is None:
            return StructureIndexResult(
                "failed",
                self.name,
                "",
                (),
                "INDEX.INPUT_HASH_INVALID",
                "Structure-index content hash must be a SHA-256 digest.",
            )

        try:
            client = self._client()
            provider_version = _provider_version(client)
            if not provider_version:
                raise PageIndexError(_PROVIDER_UNAVAILABLE)
            operation_id = build_operation_identity(
                document_version_id,
                content_hash,
                provider_version,
            )
            index_kwargs = {
                "operation_id": operation_id,
                "document_version_id": document_version_id,
                "expected_content_hash": content_hash,
                "expected_provider_version": provider_version,
            }
            raw = (
                client.index_pdf(request.source_path, **index_kwargs)
                if request.media_type == "application/pdf"
                else client.index_md(request.source_path, **index_kwargs)
            )
        except PageIndexError:
            return StructureIndexResult(
                "blocked",
                self.name,
                "",
                (),
                "INDEX.PROVIDER_UNAVAILABLE",
                _PROVIDER_UNAVAILABLE,
            )

        if _is_provider_unavailable_result(raw):
            return StructureIndexResult(
                "blocked",
                self.name,
                "",
                (),
                "INDEX.PROVIDER_UNAVAILABLE",
                _PROVIDER_UNAVAILABLE,
            )
        if not isinstance(raw, Mapping):
            return StructureIndexResult(
                "failed",
                self.name,
                "",
                (),
                "INDEX.PROVIDER_FAILED",
                _PROVIDER_FAILED,
            )
        if raw.get("error_code") == "PAGEINDEX.INPUT.HASH_INVALID":
            return StructureIndexResult(
                "failed",
                self.name,
                "",
                (),
                "INDEX.INPUT_HASH_INVALID",
                "Structure-index content hash must be a SHA-256 digest.",
            )
        if raw.get("error_code") == "PAGEINDEX.INPUT.HASH_MISMATCH":
            return StructureIndexResult(
                "failed",
                self.name,
                "",
                (),
                "INDEX.INPUT_HASH_MISMATCH",
                "Structure-index input does not match the requested content hash.",
            )
        if raw.get("error_code") == "PAGEINDEX.INPUT.IDENTITY_MISMATCH":
            return StructureIndexResult(
                "failed",
                self.name,
                "",
                (),
                "INDEX.INPUT_IDENTITY_MISMATCH",
                "Structure-index operation identity does not match the input.",
            )
        if raw.get("status") != "success":
            return StructureIndexResult(
                "failed",
                self.name,
                "",
                (),
                "INDEX.PROVIDER_FAILED",
                _PROVIDER_FAILED,
            )
        if (
            raw.get("operation_id") != operation_id
            or raw.get("provider_version") != provider_version
            or raw.get("content_hash") != content_hash
        ):
            return StructureIndexResult(
                "failed",
                self.name,
                "",
                (),
                "INDEX.RESULT_IDENTITY_MISMATCH",
                "Structure-index result identity does not match the request.",
            )
        return StructureIndexResult(
            "success",
            self.name,
            f"pageindex://{operation_id}",
            tuple(raw.get("structure", [])),
            "",
            "",
        )
