import json
from pathlib import Path

import pytest


def _scan(tmp_path: Path, relative_path: str, content: str):
    from governance.validate import validate_repository_hygiene

    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return validate_repository_hygiene(
        project_root=tmp_path,
        tracked_files=[relative_path],
        verbose=False,
    )


def test_repository_hygiene_rejects_rfc1918_url(tmp_path):
    private_url = "http://172." + "20.1.2:9000/v1"

    errors, warnings, findings = _scan(tmp_path, "config/provider.yaml", private_url)

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-RFC1918-URL"


def test_repository_hygiene_rejects_personal_absolute_path(tmp_path):
    personal_path = "/Users/" + "alice/Documents/private"

    errors, warnings, findings = _scan(tmp_path, "docs/setup.md", personal_path)

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-PERSONAL-PATH"


def test_repository_hygiene_rejects_windows_personal_absolute_path(tmp_path):
    personal_path = "C:\\" + "Users\\alice\\Documents\\private"

    errors, warnings, findings = _scan(tmp_path, "scripts/setup.ps1", personal_path)

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-PERSONAL-PATH"


def test_repository_hygiene_rejects_tracked_runtime_trace(tmp_path):
    errors, warnings, findings = _scan(tmp_path, "logs/runtime.json", "{}")

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-TRACKED-RUNTIME-LOG"


@pytest.mark.parametrize(
    "business_path",
    [
        "Q:" + r"\SharedBusiness\CustomerAlpha\contract.docx",
        "/" + "Volumes/SharedBusiness/CustomerAlpha/contract.docx",
        "/" + "mnt/business/CustomerAlpha/contract.docx",
    ],
)
def test_repository_hygiene_rejects_non_user_business_absolute_paths(
    tmp_path, business_path
):
    errors, warnings, findings = _scan(
        tmp_path,
        "docs/operations/runbook.md",
        f"source: {business_path}",
    )

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-BUSINESS-ABSOLUTE-PATH"


@pytest.mark.parametrize(
    "credential",
    [
        "postgresql://app_user:" + "v3ry-real-looking-value" + "@db.internal/app",
        'api_key = "' + "sk-" + "live-0123456789abcdef" + '"',
        '{"api_key": "' + "live_0123456789abcdef" + '"}',
        "Authorization: Bearer " + "eyJhbGciOiJIUzI1NiJ9.payload.signature",
    ],
)
def test_repository_hygiene_rejects_hardcoded_credentials(tmp_path, credential):
    errors, warnings, findings = _scan(
        tmp_path,
        "config/provider.yaml",
        credential,
    )

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-HARDCODED-CREDENTIAL"


def test_repository_hygiene_rejects_embedded_business_fixture_list(tmp_path):
    manifest = (
        "REAL_"
        + "SAMPLES = [\n"
        + "    ('CustomerAlpha/contract.docx', 'contract'),\n"
        + "]\n"
    )

    errors, warnings, findings = _scan(
        tmp_path,
        "tests/customer_" + "sample_regression.py",
        manifest,
    )

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-REAL-" + "SAMPLE-MANIFEST"


def test_repository_hygiene_allows_same_line_audited_synthetic_path(tmp_path):
    synthetic_path = "Q:" + r"\SyntheticBusiness\ProjectAlpha\contract.docx"

    errors, warnings, findings = _scan(
        tmp_path,
        "tests/test_path_contract.py",
        synthetic_path + "  # repo-hygiene: allow=synthetic-path",
    )

    assert (errors, warnings, findings) == (0, 0, [])


def test_repository_hygiene_allows_declared_synthetic_json_fixture(tmp_path):
    payload = json.dumps(
        {
            "fixture_kind": "synthetic",
            "contains_real_business_data": False,
            "samples": [
                {
                    "relative_path": "synthetic/ProjectAlpha/contract.docx",
                    "expected_category": "contract",
                }
            ],
            "example_root": "Q:" + r"\SyntheticBusiness\ProjectAlpha",
        }
    )

    errors, warnings, findings = _scan(
        tmp_path,
        "tests/fixtures/synthetic_document_manifest.json",
        payload,
    )

    assert (errors, warnings, findings) == (0, 0, [])


def test_current_tracked_repository_passes_hygiene_scan():
    from governance.validate import validate_repository_hygiene

    project_root = Path(__file__).resolve().parents[1]
    errors, warnings, findings = validate_repository_hygiene(
        project_root=project_root,
        verbose=False,
    )

    assert (errors, warnings, findings) == (0, 0, [])
