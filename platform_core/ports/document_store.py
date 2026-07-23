from typing import BinaryIO, Protocol, runtime_checkable

from platform_core.models import DocumentRef, ObjectStat


@runtime_checkable
class DocumentStore(Protocol):
    name: str

    def stat(self, ref: DocumentRef) -> ObjectStat:
        raise NotImplementedError

    def open_read(self, ref: DocumentRef) -> BinaryIO:
        raise NotImplementedError
