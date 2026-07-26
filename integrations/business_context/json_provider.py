"""Strict, caller-scoped loading for ``business_context_catalog.v1`` files."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any

from platform_core.models import BusinessContextQuery
from platform_core.storage_bindings import StorageBindingRegistry


_VERSION = "business_context_catalog.v1"
_ERROR_CODE = "BUSINESS_CONTEXT.CATALOG_INVALID"
_ROOT_FIELDS = {"schema_version", "records"}
_RECORD_FIELDS = {"id", "document_type", "parties", "facts", "documents", "path_hints"}
_PARTY_FIELDS = {"name", "tax_id"}
_FACT_FIELDS = {"contract_code", "project_code", "amount", "date"}
_DOCUMENT_FIELDS = {
    "path", "document_version_id", "content_hash", "media_type", "page_count",
    "requires_structure_index",
}


class BusinessContextCatalogError(ValueError):
    """The explicitly supplied catalog cannot safely be used."""

    code = _ERROR_CODE

    def __init__(self) -> None:
        super().__init__("Business context catalog is invalid.")


class JsonBusinessContextProvider:
    """Load exactly one declared JSON catalog; never discover business files."""

    def __init__(
        self,
        catalog_path: str | Path,
        storage_bindings: StorageBindingRegistry | None = None,
        *,
        binding_registry: StorageBindingRegistry | None = None,
    ) -> None:
        if storage_bindings is None:
            storage_bindings = binding_registry
        if storage_bindings is None or (binding_registry is not None and binding_registry is not storage_bindings):
            raise BusinessContextCatalogError()
        self.catalog_path = Path(catalog_path).expanduser().resolve()
        self.storage_bindings = storage_bindings
        try:
            payload = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            self._records = self._validate_catalog(payload)
        except (BusinessContextCatalogError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise BusinessContextCatalogError() from None

    def _validate_catalog(self, raw: object) -> tuple[dict[str, Any], ...]:
        catalog = _mapping(raw)
        _only(catalog, _ROOT_FIELDS)
        if catalog.get("schema_version") != _VERSION or not isinstance(catalog.get("records"), list):
            raise BusinessContextCatalogError()
        records: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_document_versions: set[str] = set()
        for raw_record in catalog["records"]:
            record = _mapping(raw_record)
            _only(record, _RECORD_FIELDS)
            record_id = record.get("id")
            if not _string(record_id) or record_id in seen or not _string(record.get("document_type")):
                raise BusinessContextCatalogError()
            seen.add(record_id)
            parties = self._parties(record.get("parties"))
            facts = self._facts(record.get("facts"))
            documents = self._documents(record.get("documents"), seen_document_versions)
            hints = record.get("path_hints", [])
            if not isinstance(hints, list) or not all(_string(hint) for hint in hints):
                raise BusinessContextCatalogError()
            records.append({
                "id": record_id,
                "document_type": record["document_type"],
                "parties": parties,
                "facts": facts,
                "documents": documents,
                "path_hints": list(hints),
            })
        return tuple(records)

    @staticmethod
    def _parties(raw: object) -> dict[str, dict[str, str]]:
        parties = _mapping(raw)
        if set(parties) != {"buyer", "seller"}:
            raise BusinessContextCatalogError()
        result: dict[str, dict[str, str]] = {}
        for role in ("buyer", "seller"):
            party = _mapping(parties[role])
            _only(party, _PARTY_FIELDS)
            if not all(_string(value) for value in party.values()):
                raise BusinessContextCatalogError()
            result[role] = dict(party)
        return result

    @staticmethod
    def _facts(raw: object) -> dict[str, str]:
        facts = _mapping(raw)
        _only(facts, _FACT_FIELDS)
        if not all(_string(value) for value in facts.values()):
            raise BusinessContextCatalogError()
        return dict(facts)

    def _documents(
        self, raw: object, seen_document_versions: set[str]
    ) -> list[dict[str, Any]]:
        if not isinstance(raw, list) or not raw:
            raise BusinessContextCatalogError()
        result: list[dict[str, Any]] = []
        for item in raw:
            document = _mapping(item)
            _only(document, _DOCUMENT_FIELDS)
            document_version_id = document.get("document_version_id")
            if not _string(document.get("path")) or not _string(document_version_id):
                raise BusinessContextCatalogError()
            if document_version_id in seen_document_versions:
                raise BusinessContextCatalogError()
            seen_document_versions.add(document_version_id)
            digest = document.get("content_hash")
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
                raise BusinessContextCatalogError()
            if not _string(document.get("media_type")):
                raise BusinessContextCatalogError()
            page_count = document.get("page_count", 0)
            if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 0:
                raise BusinessContextCatalogError()
            requires = document.get("requires_structure_index", False)
            if not isinstance(requires, bool):
                raise BusinessContextCatalogError()
            try:
                ref = self.storage_bindings.document_ref_from_path(document["path"])
            except Exception:
                raise BusinessContextCatalogError() from None
            result.append({
                "path": str(Path(document["path"]).expanduser().resolve()),
                "document_ref": ref,
                "document_version_id": document["document_version_id"],
                "content_hash": digest.lower(),
                "media_type": document["media_type"],
                "page_count": page_count,
                "requires_structure_index": requires,
            })
        return result

    def search(self, query: BusinessContextQuery) -> Sequence[Mapping[str, Any]]:
        if not isinstance(query, BusinessContextQuery) or not isinstance(query.candidate_fields, Mapping):
            return ()
        return tuple(deepcopy(record) for record in self._records if _matches(record, query.candidate_fields))


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise BusinessContextCatalogError()
    return value


def _only(value: Mapping[str, Any], allowed: set[str]) -> None:
    if set(value).difference(allowed):
        raise BusinessContextCatalogError()


def _string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _matches(record: Mapping[str, Any], fields: Mapping[str, Any]) -> bool:
    for role in ("buyer", "seller"):
        queried = fields.get(role)
        if not isinstance(queried, Mapping):
            queried = {
                field: fields.get(f"{role}_{field}")
                for field in ("tax_id", "name")
            }
        if isinstance(queried, Mapping):
            for field in ("tax_id", "name"):
                if _same(field, queried.get(field), record["parties"][role].get(field)):
                    return True
    for field in ("contract_code", "project_code", "amount", "date"):
        if _same(field, fields.get(field), record["facts"].get(field)):
            return True
    path_hint = fields.get("path_hint", fields.get("path"))
    return isinstance(path_hint, str) and any(_path_match(path_hint, hint) for hint in record["path_hints"])


def _same(field: str, left: object, right: object) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    if field == "name":
        return _name(left) == _name(right)
    if field in {"tax_id", "contract_code", "project_code"}:
        return re.sub(r"[\s-]+", "", left).upper() == re.sub(r"[\s-]+", "", right).upper()
    return left.strip() == right.strip()


def _name(value: str) -> str:
    return re.sub(r"[\s()（）,，.。]+", "", value).upper()


def _path_match(left: str, right: str) -> bool:
    left, right = left.replace("\\", "/").casefold().strip("/"), right.replace("\\", "/").casefold().strip("/")
    return bool(left and right and (left in right or right in left))


__all__ = ["BusinessContextCatalogError", "JsonBusinessContextProvider"]
