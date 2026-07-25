from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "raw_value",
    [
        r"\\server\share\runtime",  # repo-hygiene: allow=synthetic-path
        "//server/share/runtime",  # repo-hygiene: allow=synthetic-path
        "smb://server/share/runtime",
        "nfs://server/export/runtime",
        "afp://server/share/runtime",
    ],
)
def test_node_local_validator_rejects_obvious_network_locations(
    tmp_path, raw_value
):
    from platform_core.path_locality import NodeLocalPathError, ensure_node_local_path

    with pytest.raises(NodeLocalPathError, match="runtime_workspace must be node-local"):
        ensure_node_local_path(
            field_name="runtime_workspace",
            raw_value=raw_value,
            resolved_path=tmp_path / "resolved-runtime",
            business_root=tmp_path / "business",
        )


@pytest.mark.parametrize("relative_path", [".", "runtime", "nested/runtime"])
def test_node_local_validator_rejects_business_root_and_descendants(
    tmp_path, relative_path
):
    from platform_core.path_locality import NodeLocalPathError, ensure_node_local_path

    business_root = (tmp_path / "business").resolve()
    candidate = (business_root / relative_path).resolve()

    with pytest.raises(
        NodeLocalPathError,
        match="projection_root must not be inside business_root",
    ):
        ensure_node_local_path(
            field_name="projection_root",
            raw_value=candidate,
            resolved_path=candidate,
            business_root=business_root,
        )


def test_node_local_validator_accepts_local_sibling_with_shared_prefix(tmp_path):
    from platform_core.path_locality import ensure_node_local_path

    business_root = (tmp_path / "business").resolve()
    local_runtime = (tmp_path / "business-runtime").resolve()

    result = ensure_node_local_path(
        field_name="runtime_workspace",
        raw_value=local_runtime,
        resolved_path=local_runtime,
        business_root=business_root,
    )

    assert result == local_runtime
