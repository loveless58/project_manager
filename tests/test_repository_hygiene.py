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


@pytest.mark.parametrize(
    "credential_name",
    [
        "PROJECT_MANAGER_" + "API_KEY",
        "DATABASE_" + "PASSWORD",
        "SERVICE_" + "ACCESS_TOKEN",
        "OAUTH_" + "CLIENT_SECRET",
    ],
)
def test_repository_hygiene_rejects_prefixed_credential_variables(
    tmp_path, credential_name
):
    assignment = credential_name + ' = "' + "live-value-0123456789" + '"'

    errors, warnings, findings = _scan(
        tmp_path,
        "config/provider.py",
        assignment,
    )

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-HARDCODED-CREDENTIAL"


def test_repository_hygiene_allows_prefixed_credential_env_placeholder(tmp_path):
    assignment = "PROJECT_MANAGER_" + "API_KEY" + ' = "${PROVIDER_KEY}"'

    errors, warnings, findings = _scan(
        tmp_path,
        "config/provider.py",
        assignment,
    )

    assert (errors, warnings, findings) == (0, 0, [])


@pytest.mark.parametrize(
    "business_path",
    [
        "Q:" + r"\Business(Archive)\CustomerAlpha\contract.docx",
        "Q:" + r"\Business[Archive]\CustomerAlpha\contract.docx",
        "Q:" + r"\业务测试区\客户甲\合同.docx",
        "Q:" + r"\\BusinessArchive\\CustomerAlpha\\contract.docx",
    ],
)
def test_repository_hygiene_does_not_infer_synthetic_path_from_substrings(
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


def test_repository_hygiene_rejects_structured_real_sample_cases(tmp_path):
    manifest = (
        "REAL_"
        + "SAMPLE_CASES = [\n"
        + "    {\"source_file\": \"CustomerAlpha/contract.docx\", "
        + "\"expected_category\": \"contract\"},\n"
        + "]\n"
    )

    errors, warnings, findings = _scan(
        tmp_path,
        "tests/customer_" + "sample_cases.py",
        manifest,
    )

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-REAL-" + "SAMPLE-MANIFEST"


def test_repository_hygiene_allows_real_sample_policy_without_embedded_data(tmp_path):
    policy = "Real sample regression must use an ignored local manifest."

    errors, warnings, findings = _scan(
        tmp_path,
        "docs/real_" + "sample_policy.md",
        policy,
    )

    assert (errors, warnings, findings) == (0, 0, [])


def test_repository_hygiene_rejects_unsanitized_structured_business_content(
    tmp_path,
):
    content = (
        "{\n"
        + '  "project_' + 'name": "Commercial Delivery Upgrade",\n'
        + '  "customer_' + 'name": "Example Holdings",\n'
        + '  "sales_' + 'owner": "Person A",\n'
        + '  "invoice_' + 'number": "INV-2026-884201"\n'
        + "}\n"
    )

    errors, warnings, findings = _scan(
        tmp_path,
        "tests/business_record.py",
        content,
    )

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-REAL-BUSINESS-CONTENT"


def test_repository_hygiene_allows_explicitly_synthetic_business_content(tmp_path):
    content = (
        "# repo-hygiene: data=synthetic\n"
        + "{\n"
        + '  "project_' + 'name": "合成项目Alpha",\n'
        + '  "customer_' + 'name": "合成客户甲",\n'
        + '  "sales_' + 'owner": "合成人员甲",\n'
        + '  "invoice_' + 'number": "SYN-INVOICE-001"\n'
        + "}\n"
    )

    errors, warnings, findings = _scan(
        tmp_path,
        "tests/business_record.py",
        content,
    )

    assert (errors, warnings, findings) == (0, 0, [])


@pytest.mark.parametrize(
    "relative_path",
    [
        "business_rules/document_parse/kb.md",
        "tests/test_loop.py",
    ],
)
def test_tracked_business_examples_contain_no_unsanitized_content(relative_path):
    from governance.validate import validate_repository_hygiene

    project_root = Path(__file__).resolve().parents[1]
    report = validate_repository_hygiene(
        project_root=project_root,
        tracked_files=[relative_path],
        verbose=False,
    )

    assert not any(
        finding["id"] == "REPO-REAL-BUSINESS-CONTENT"
        for finding in report.findings
    )


@pytest.mark.parametrize("separator", [": ", " = ", "="])
def test_repository_hygiene_rejects_unquoted_prefixed_credentials(
    tmp_path, separator
):
    credential_name = "PROJECT_MANAGER_" + "ACCESS_TOKEN"
    content = credential_name + separator + "production-value-0123456789"

    errors, warnings, findings = _scan(tmp_path, "config/provider.env", content)

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-HARDCODED-CREDENTIAL"


@pytest.mark.parametrize(
    "placeholder",
    ["${PROVIDER_TOKEN}", "{{ provider_token }}", "env:PROVIDER_TOKEN"],
)
def test_repository_hygiene_allows_unquoted_credential_placeholders(
    tmp_path, placeholder
):
    credential_name = "PROJECT_MANAGER_" + "ACCESS_TOKEN"
    content = credential_name + "=" + placeholder

    errors, warnings, findings = _scan(tmp_path, "config/provider.env", content)

    assert (errors, warnings, findings) == (0, 0, [])


@pytest.mark.parametrize(
    "reference",
    [
        "$PROVIDER_TOKEN",
        "${PROVIDER_TOKEN}",
        "%PROVIDER_TOKEN%",
        'os.environ["PROVIDER_TOKEN"]',
        'os.getenv("PROVIDER_TOKEN")',
        "settings.provider_token",
    ],
)
def test_repository_hygiene_allows_explicit_credential_references(
    tmp_path, reference
):
    credential_name = "PROJECT_MANAGER_" + "ACCESS_TOKEN"
    content = credential_name + " = " + reference

    errors, warnings, findings = _scan(tmp_path, "config/provider.py", content)

    assert (errors, warnings, findings) == (0, 0, [])


@pytest.mark.parametrize(
    "literal",
    [
        "production-value-0123456789",
        '"production-value-0123456789"',
    ],
)
def test_repository_hygiene_rejects_prefixed_credential_literals(
    tmp_path, literal
):
    credential_name = "PROJECT_MANAGER_" + "ACCESS_TOKEN"
    content = credential_name + " = " + literal

    errors, warnings, findings = _scan(tmp_path, "config/provider.py", content)

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-HARDCODED-CREDENTIAL"


def test_repository_hygiene_rejects_local_business_json_without_name_hint(tmp_path):
    payload = json.dumps(
        {
            "fixture_kind": "local_" + "business",
            "samples": [
                {
                    "source_file": "CustomerAlpha/contract.docx",
                    "expected_category": "contract",
                }
            ],
        }
    )

    errors, warnings, findings = _scan(
        tmp_path,
        "tests/fixtures/import_manifest.json",
        payload,
    )

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-REAL-" + "SAMPLE-MANIFEST"


@pytest.mark.parametrize(
    "content",
    [
        "签约主体：" + "示例科技有限" + "公司",
        "采购人：" + "示例科技" + "公司",
        "销售负责人：" + "Person " + "Omega",
        "项目名称：" + "Commercial " + "Upgrade",
        "合同编号：" + "ABC" + "-2026-0001",
        'subject_name = "' + "Commercial Delivery" + '"',
    ],
)
def test_repository_hygiene_rejects_single_explicit_business_value(
    tmp_path, content
):
    errors, warnings, findings = _scan(
        tmp_path,
        "tests/business_fragment.txt",
        content,
    )

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-REAL-BUSINESS-CONTENT"


@pytest.mark.parametrize(
    "content",
    [
        "签约主体：合成科技有限公司",
        "销售负责人：虚构人员甲",
        "项目名称：合成项目Alpha",
        "合同编号：SYN-CONTRACT-001",
        "项目名称：{project_name}",
        "project_name = record.project_name",
        "project_name = payload['project_name']",
        'project_name = "..."',
        "客户：{record.get('customer_name')}",
        "f\"- 客户: {record.get('basic_info', {}).get('客户', 'N/A')}\"",
        'subject_name = "project_manager"',
        "开户银行：合成银行",
        r"前缀\n开户银行：合成银行",
    ],
)
def test_repository_hygiene_allows_single_synthetic_business_value(
    tmp_path, content
):
    errors, warnings, findings = _scan(
        tmp_path,
        "tests/business_fragment.txt",
        content,
    )

    assert (errors, warnings, findings) == (0, 0, [])


@pytest.mark.parametrize(
    "content",
    [
        "project_" + 'name = "Commercial Delivery"',
        "project_" + 'name: str = "Commercial Delivery"',
        'record = {"project_' + 'name": "Commercial Delivery"}',
        "source_" + 'kind = "real"',
        'record = {"source_' + 'kind": "real"}',
        "project_" + 'name = "Customer合成Migration"',
    ],
)
def test_repository_hygiene_rejects_python_literal_business_bindings(
    tmp_path, content
):
    errors, warnings, findings = _scan(
        tmp_path,
        "src/business_fixture.py",
        content,
    )

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-REAL-BUSINESS-CONTENT"


@pytest.mark.parametrize(
    "content",
    [
        "project_name: str",
        "def load(project_name: str) -> str:\n    return project_name",
        "project_name = record.project_name",
        'project_name = payload["project_name"]',
        'project_name = f"{prefix}-{suffix}"',
        "project_name = Field(default=None)",
        'project_name = "合成项目Alpha"',
        'source_kind = "synthetic"',
    ],
)
def test_repository_hygiene_allows_python_dynamic_or_synthetic_bindings(
    tmp_path, content
):
    errors, warnings, findings = _scan(
        tmp_path,
        "src/business_fixture.py",
        content,
    )

    assert (errors, warnings, findings) == (0, 0, [])


def test_repository_hygiene_rejects_declared_production_provenance(tmp_path):
    content = "数据来源：" + "真实" + "样本"

    errors, warnings, findings = _scan(
        tmp_path,
        "contracts/data_provenance.md",
        content,
    )

    assert errors == 1
    assert warnings == 0
    assert findings[0]["id"] == "REPO-REAL-BUSINESS-CONTENT"


def test_repository_hygiene_allows_declared_synthetic_provenance(tmp_path):
    errors, warnings, findings = _scan(
        tmp_path,
        "contracts/data_provenance.md",
        "数据来源：合成样本",
    )

    assert (errors, warnings, findings) == (0, 0, [])
