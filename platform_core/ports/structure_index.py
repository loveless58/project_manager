from typing import Protocol, runtime_checkable

from platform_core.models import CapabilityReport, StructureIndexRequest, StructureIndexResult


@runtime_checkable
class StructureIndex(Protocol):
    name: str

    def probe(self) -> CapabilityReport:
        raise NotImplementedError

    def index(self, request: StructureIndexRequest) -> StructureIndexResult:
        raise NotImplementedError
