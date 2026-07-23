"""PageIndex integration exports."""

from .pageindex_client import PageIndexClient, PageIndexError
from .structure_index import PageIndexStructureIndex

__all__ = ["PageIndexClient", "PageIndexError", "PageIndexStructureIndex"]
