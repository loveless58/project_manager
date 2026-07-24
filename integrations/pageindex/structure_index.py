"""StructureIndex port adapter backed by the optional PageIndex CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Union

from platform_core.models import CapabilityReport, StructureIndexRequest, StructureIndexResult

from .pageindex_client import (
    PageIndexClient,
    PageIndexError,
    build_operation_identity,
)


ClientFactory = Callable[[str], PageIndexClient]
_PROVIDER_UNAVAILABLE = "PageIndex provider is unavailable."
_PROVIDER_FAILED = "PageIndex provider failed."
_UNAVAILABLE_RESULT_CODES = {"PAGEINDEX.CONFIG.MISSING"}
_UNAVAILABLE_RESULT_PREFIX = "PAGEINDEX.RUNTIME."


def _is_provider_unavailable_result(raw: object) -> bool:
    if not isinstance(raw, dict):
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
        return str(getattr(client, "provider_version", "pageindex-cli-v1:legacy"))

    def probe(self) -> CapabilityReport:
        try:
            client = self._client()
            client.check_environment()
        except PageIndexError:
            return CapabilityReport("blocked", self.name, "", _PROVIDER_UNAVAILABLE)
        provider_version = str(
            getattr(client, "provider_version", "pageindex-cli-v1:legacy")
        )
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

        try:
            client = self._client()
            provider_version = str(
                getattr(client, "provider_version", "pageindex-cli-v1:legacy")
            )
            operation_id = build_operation_identity(
                request.document_version_id,
                request.content_hash,
                provider_version,
            )
            index_kwargs = {
                "operation_id": operation_id,
                "document_version_id": request.document_version_id,
                "expected_content_hash": request.content_hash,
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
        if raw.get("error_code") == "PAGEINDEX.INPUT.HASH_MISMATCH":
            return StructureIndexResult(
                "failed",
                self.name,
                "",
                (),
                "INDEX.INPUT_HASH_MISMATCH",
                "Structure-index input does not match the requested content hash.",
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
        if raw.get("operation_id") != operation_id:
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
