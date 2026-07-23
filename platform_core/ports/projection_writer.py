from typing import Protocol, runtime_checkable

from platform_core.models import ProjectionRef, ProjectionRequest


@runtime_checkable
class ProjectionWriter(Protocol):
    name: str

    def write(self, request: ProjectionRequest) -> ProjectionRef:
        raise NotImplementedError
