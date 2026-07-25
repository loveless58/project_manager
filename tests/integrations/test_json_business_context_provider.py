import json

import pytest

from platform_core.models import BusinessContextQuery
from platform_core.storage_bindings import StorageBinding, StorageBindingRegistry


def _registry(tmp_path):
    business_root = tmp_path / "business"
    business_root.mkdir()
    return business_root, StorageBindingRegistry(
        [
            StorageBinding(
                "business-source",
                "local",
                "node",
                "business://source/",
                business_root,
                ("source",),
                True,
                False,
            )
        ]
    )


def _catalog(document_path, **record_overrides):
    record = {
        "id": "contract-001",
        "document_type": "合同",
        "parties": {
            "buyer": {"tax_id": "91310001", "name": "合成甲方有限公司"},
            "seller": {"tax_id": "91320002", "name": "合成乙方有限公司"},
        },
        "facts": {"contract_code": "SYN-CONTRACT-001"},
        "documents": [
            {
                "path": str(document_path),
                "document_version_id": "contract-v1",
                "content_hash": "a" * 64,
                "media_type": "application/pdf",
                "page_count": 30,
            }
        ],
    }
    record.update(record_overrides)
    return {"schema_version": "business_context_catalog.v1", "records": [record]}


def _write_catalog(tmp_path, payload):
    catalog_path = tmp_path / "explicit-catalog.json"
    catalog_path.write_text(json.dumps(payload), encoding="utf-8")
    return catalog_path


def test_provider_loads_only_explicit_catalog_and_resolves_document_to_binding(tmp_path):
    from integrations.business_context.json_provider import JsonBusinessContextProvider

    business_root, registry = _registry(tmp_path)
    document = business_root / "contracts" / "contract.pdf"
    document.parent.mkdir()
    document.touch()
    ignored = business_root / "business_context_catalog.v1.json"
    ignored.write_text(json.dumps({"schema_version": "business_context_catalog.v1", "records": []}))
    provider = JsonBusinessContextProvider(_write_catalog(tmp_path, _catalog(document)), registry)

    records = provider.search(BusinessContextQuery("发票", {"contract_code": "SYN-CONTRACT-001"}, ()))

    assert [record["id"] for record in records] == ["contract-001"]
    assert records[0]["documents"][0]["document_ref"].binding_id == "business-source"
    assert records[0]["documents"][0]["document_ref"].logical_uri == "business://source/contracts/contract.pdf"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["records"].append(dict(payload["records"][0])),
        lambda payload: payload["records"][0].update({"unexpected": True}),
        lambda payload: payload["records"][0].pop("parties"),
        lambda payload: payload["records"][0]["documents"][0].update({"path": "/outside/contract.pdf"}),
    ],
)
def test_provider_rejects_invalid_catalog_with_stable_error(tmp_path, mutate):
    from integrations.business_context.json_provider import (
        BusinessContextCatalogError,
        JsonBusinessContextProvider,
    )

    business_root, registry = _registry(tmp_path)
    document = business_root / "contract.pdf"
    document.touch()
    payload = _catalog(document)
    mutate(payload)

    with pytest.raises(BusinessContextCatalogError) as exc_info:
        JsonBusinessContextProvider(_write_catalog(tmp_path, payload), registry)

    assert exc_info.value.code == "BUSINESS_CONTEXT.CATALOG_INVALID"


def test_provider_rejects_duplicate_document_version_identity(tmp_path):
    from integrations.business_context.json_provider import (
        BusinessContextCatalogError,
        JsonBusinessContextProvider,
    )

    business_root, registry = _registry(tmp_path)
    document = business_root / "contract.pdf"
    document.touch()
    payload = _catalog(document)
    duplicate = dict(payload["records"][0])
    duplicate["id"] = "contract-002"
    duplicate["documents"] = [dict(payload["records"][0]["documents"][0])]
    payload["records"].append(duplicate)

    with pytest.raises(BusinessContextCatalogError) as exc_info:
        JsonBusinessContextProvider(_write_catalog(tmp_path, payload), registry)

    assert exc_info.value.code == "BUSINESS_CONTEXT.CATALOG_INVALID"
