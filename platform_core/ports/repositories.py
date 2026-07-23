from typing import Generic, Optional, Protocol, TypeVar, runtime_checkable


T = TypeVar("T")


@runtime_checkable
class Repository(Protocol, Generic[T]):
    def get(self, entity_id: str) -> Optional[T]:
        raise NotImplementedError

    def add(self, entity: T) -> None:
        raise NotImplementedError


@runtime_checkable
class UnitOfWork(Protocol):
    def __enter__(self) -> "UnitOfWork":
        raise NotImplementedError

    def __exit__(self, exc_type, exc, traceback) -> None:
        raise NotImplementedError

    def commit(self) -> None:
        raise NotImplementedError

    def rollback(self) -> None:
        raise NotImplementedError
