"""StructureIndex port adapter backed by the optional PageIndex CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional, Union

from platform_core.models import CapabilityReport, StructureIndexRequest, StructureIndexResult

from .pageindex_client import PageIndexClient, PageIndexError


ClientFactory = Callable[[str], PageIndexClient]
_PROVIDER_UNAVAILABLE = "PageIndex provider is unavailable."
_PROVIDER_FAILED = "PageIndex provider failed."


class PageIndexStructureIndex:
    """Map sanitized PageIndex results onto the StructureIndex port."""

    name = "pageindex"

    def __init__(
        self,
        pageindex_dir: Union[str, Path],
        client_factory: Optional[ClientFactory] = None,
    ) -> None:
        self.pageindex_dir = str(Path(pageindex_dir).expanduser().resolve())
        self.client_factory = client_factory or PageIndexClient

    def _client(self) -> PageIndexClient:
        return self.client_factory(self.pageindex_dir)

    def probe(self) -> CapabilityReport:
        try:
            self._client().check_environment()
        except PageIndexError:
            return CapabilityReport("blocked", self.name, "", _PROVIDER_UNAVAILABLE)
        return CapabilityReport("ready", self.name, "configured", "ready")

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
            raw = (
                client.index_pdf(request.source_path)
                if request.media_type == "application/pdf"
                else client.index_md(request.source_path)
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

        if raw.get("status") != "success":
            return StructureIndexResult(
                "failed",
                self.name,
                "",
                (),
                "INDEX.PROVIDER_FAILED",
                _PROVIDER_FAILED,
            )
        return StructureIndexResult(
            "success",
            self.name,
            raw.get("structure_json_path", ""),
            tuple(raw.get("structure", [])),
            "",
            "",
        )
