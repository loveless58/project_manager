from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from platform_core.models import BusinessContextQuery


@runtime_checkable
class BusinessContextProvider(Protocol):
    """Read candidates from one caller-selected business context source."""

    def search(self, query: BusinessContextQuery) -> Sequence[Mapping[str, Any]]:
        raise NotImplementedError


__all__ = ["BusinessContextProvider"]
