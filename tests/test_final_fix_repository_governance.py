from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SWIFT_BRIDGE = "integrations/macos_vision_bridge/swift_ocr_bridge"


def _scan_bytes(tmp_path: Path, relative_path: str, payload: bytes):
    from governance.repository_hygiene import validate_repository_hygiene

    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return validate_repository_hygiene(
        project_root=tmp_path,
        tracked_files=[relative_path],
        verbose=False,
    )


def _finding_ids(report) -> set[str]:
    return {finding["id"] for finding in report.findings}


@pytest.mark.parametrize(
    ("relative_path", "payload", "finding_id"),
    [
        ("customer/contract.pdf", b"%PDF-1.7\x00customer", "REPO-TRACKED-SENSITIVE-FILE"),
        (
            "config/server.key",
            b"-----BEGIN " + b"PRIVATE KEY-----\nsynthetic\n",
            "REPO-TRACKED-SENSITIVE-FILE",
        ),
        ("runtime/state.sqlite3", b"SQLite format 3\x00", "REPO-TRACKED-SENSITIVE-FILE"),
        ("NOTICE.unknown", b"plain text", "REPO-TRACKED-UNSUPPORTED-FILE"),
        ("bin/arbitrary_tool", b"\x7fELF\x00binary", "REPO-TRACKED-BINARY-FORBIDDEN"),
    ],
)
def test_tracked_file_policy_is_default_deny(
    tmp_path, relative_path, payload, finding_id
):
    report = _scan_bytes(tmp_path, relative_path, payload)

    assert finding_id in _finding_ids(report)
    assert report.errors >= 1


def test_only_exact_swift_bridge_binary_is_allowlisted():
    from governance.repository_hygiene import validate_repository_hygiene

    report = validate_repository_hygiene(
        project_root=PROJECT_ROOT,
        tracked_files=[SWIFT_BRIDGE],
        verbose=False,
    )

    assert report.errors == 0
    assert report.skipped_binary_count == 1


def test_swift_bridge_allowlist_rejects_same_binary_at_another_path(tmp_path):
    payload = (PROJECT_ROOT / SWIFT_BRIDGE).read_bytes()

    report = _scan_bytes(tmp_path, "bin/swift_ocr_bridge", payload)

    assert _finding_ids(report) == {"REPO-TRACKED-BINARY-FORBIDDEN"}


def test_swift_bridge_allowlist_rejects_tampered_payload(tmp_path):
    payload = bytearray((PROJECT_ROOT / SWIFT_BRIDGE).read_bytes())
    payload[-1] ^= 1

    report = _scan_bytes(tmp_path, SWIFT_BRIDGE, bytes(payload))

    assert _finding_ids(report) == {"REPO-TRACKED-BINARY-ALLOWLIST-MISMATCH"}


@pytest.mark.parametrize(
    "credential_name",
    [
        "NODE_TOKEN",
        "REFRESH_TOKEN",
        "SECRET_KEY",
        "SERVICE_CREDENTIAL",
        "AUTH_COOKIE",
        "USER_SESSION",
        "webhookSecret",
    ],
)
def test_credential_vocabulary_rejects_static_rhs(tmp_path, credential_name):
    content = credential_name + ' = "' + "production-value-0123456789" + '"'

    report = _scan_bytes(tmp_path, "config/provider.py", content.encode())

    assert "REPO-HARDCODED-CREDENTIAL" in _finding_ids(report)


def test_credential_vocabulary_rejects_static_concatenation(tmp_path):
    name = "NODE_" + "TOKEN"
    content = name + ' = "production-value-" + "0123456789"'

    report = _scan_bytes(tmp_path, "config/provider.py", content.encode())

    assert "REPO-HARDCODED-CREDENTIAL" in _finding_ids(report)


def test_credential_vocabulary_covers_python_binding_shapes(tmp_path):
    value = "production-value-0123456789"
    contents = [
        "self." + "node_token = " + repr(value),
        "record[" + repr("secret_key") + "] = " + repr(value),
        "record = {" + repr("service_credential") + ": " + repr(value) + "}",
        "Provider(auth_cookie=" + repr(value) + ")",
        "user_session: str = " + repr(value),
    ]

    for content in contents:
        report = _scan_bytes(
            tmp_path,
            "config/provider.py",
            content.encode(),
        )
        assert "REPO-HARDCODED-CREDENTIAL" in _finding_ids(report), content


def test_credential_vocabulary_preserves_camel_and_acronym_boundaries(tmp_path):
    value = "production-value-0123456789"
    contents = [
        "Provider(webhook" + "Secret=" + repr(value) + ")",
        "record = {" + repr("webhook" + "Secret") + ": " + repr(value) + "}",
        "DB" + "Session = " + repr(value),
        "JWT" + "Token = " + repr(value),
    ]

    for content in contents:
        report = _scan_bytes(tmp_path, "config/provider.py", content.encode())
        assert "REPO-HARDCODED-CREDENTIAL" in _finding_ids(report), content


def test_credential_vocabulary_covers_yaml_lists_and_shell_export(tmp_path):
    value = "production-value-0123456789"
    cases = [
        ("config/provider.yaml", "- auth_" + "token: " + repr(value)),
        ("scripts/run.sh", "export AUTH_" + "TOKEN=" + repr(value)),
    ]

    for relative_path, content in cases:
        report = _scan_bytes(tmp_path, relative_path, content.encode())
        assert "REPO-HARDCODED-CREDENTIAL" in _finding_ids(report), content


def test_credential_vocabulary_covers_compact_json_and_allows_placeholder(tmp_path):
    fixture_value = "-".join(("production", "value", "0123456789"))
    rejected = json.dumps(
        {"auth_token": fixture_value, "enabled": False},
        separators=(",", ":"),
    )
    allowed = json.dumps(
        {"auth_token": "${AUTH_TOKEN}", "enabled": False},
        separators=(",", ":"),
    )

    rejected_report = _scan_bytes(tmp_path, "config/provider.json", rejected.encode())
    allowed_report = _scan_bytes(tmp_path, "config/provider.json", allowed.encode())

    assert "REPO-HARDCODED-CREDENTIAL" in _finding_ids(rejected_report)
    assert allowed_report.errors == 0


def _credential_json_parser_edge_case(kind: str) -> str:
    field = '"' + "auth_" + "token" + '"'
    fixture_value = "-".join(("production", "value", "0123456789"))
    static_binding = field + ":" + json.dumps(fixture_value)
    if kind == "duplicate":
        dynamic_binding = field + ":" + json.dumps("${AUTH_TOKEN}")
        return "{" + static_binding + "," + dynamic_binding + "}"
    if kind == "malformed":
        return "{" + static_binding + " garbage}"
    raise AssertionError(f"unknown edge case: {kind}")


@pytest.mark.parametrize("kind", ["duplicate", "malformed"])
def test_json_parser_edges_fail_closed_in_main_scanner(tmp_path, kind):
    report = _scan_bytes(
        tmp_path,
        "config/provider.json",
        _credential_json_parser_edge_case(kind).encode(),
    )

    assert report.errors > 0, report.findings


@pytest.mark.parametrize(
    "content",
    [
        'NODE_TOKEN = os.environ["NODE_TOKEN"]',
        'SECRET_KEY = os.getenv("SECRET_KEY")',
        "SERVICE_CREDENTIAL = settings.service_credential",
        'AUTH_COOKIE = "${AUTH_COOKIE}"',
        'USER_SESSION = "<injected-at-runtime>"',
        'API_KEY = "test-key"',
        'token_budget = "120000"',
        'credential_name = "NODE_TOKEN"',
        'public_key = "ssh-rsa documentation-only"',
        'session_timeout = "30-seconds"',
        'cookie_policy = "strict-same-site"',
        'cache_key = "document-version-content-hash"',
        'content_hash = "384c1fabeaccec7133f1563a9681c2e5dbd1fceee1b22edc9f4138e78bb114f7"',
    ],
)
def test_credential_policy_preserves_dynamic_references_and_false_positives(
    tmp_path, content
):
    report = _scan_bytes(tmp_path, "config/provider.py", content.encode())

    assert report.errors == 0, report.findings


@pytest.mark.parametrize(
    "secret",
    [
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "ghp_" + "A1b2" * 9,
        "github_pat_" + "A1b2" * 10,
        "sk-proj-" + "A1b2" * 10,
        "-----BEGIN " + "PRIVATE KEY-----\nsynthetic-material",
    ],
)
def test_known_credential_prefixes_are_rejected_without_name_hint(tmp_path, secret):
    report = _scan_bytes(
        tmp_path,
        "docs/provider-example.md",
        ("value: " + secret).encode(),
    )

    assert "REPO-HARDCODED-CREDENTIAL" in _finding_ids(report)


def test_high_entropy_security_material_is_rejected_but_hash_is_not(tmp_path):
    high_entropy = "uN4@zQ8#pL2$xR6!vT0%mK7&wC3*eH9?"
    content = 'AUTH_MATERIAL = "' + high_entropy + '"'

    rejected = _scan_bytes(tmp_path, "config/provider.py", content.encode())
    allowed = _scan_bytes(
        tmp_path,
        "config/hash.py",
        (
            'CONTENT_HASH = "'
            + "384c1fabeaccec7133f1563a9681c2e5dbd1fceee1b22edc9f4138e78bb114f7"
            + '"'
        ).encode(),
    )

    assert "REPO-HARDCODED-CREDENTIAL" in _finding_ids(rejected)
    assert allowed.errors == 0


def _unc_samples() -> list[str]:
    slash = "\\"
    return [
        slash * 2 + "nas01" + slash + "share" + slash + "customer" + slash + "contract.pdf",
        "//" + "nas01/share/customer/contract.pdf",
        slash * 2 + "?" + slash + "UNC" + slash + "nas01" + slash + "share" + slash + "customer" + slash + "contract.pdf",
    ]


@pytest.mark.parametrize("unc_path", _unc_samples())
def test_repository_hygiene_rejects_unc_business_paths(tmp_path, unc_path):
    report = _scan_bytes(
        tmp_path,
        "docs/operations/runbook.md",
        ("source: " + unc_path).encode(),
    )

    assert "REPO-BUSINESS-ABSOLUTE-PATH" in _finding_ids(report)


@pytest.mark.parametrize("unc_path", _unc_samples())
def test_unc_synthetic_exemption_must_be_exact_and_same_line(tmp_path, unc_path):
    marker = "repo-hygiene: allow=synthetic-path"
    valid = "source: " + unc_path + "  # " + marker
    invalid_prior_line = "# " + marker + "\nsource: " + unc_path
    invalid_not_trailing = "source: " + unc_path + "  # " + marker + "; ignored"

    allowed = _scan_bytes(tmp_path, "tests/path_fixture.md", valid.encode())
    rejected_prior = _scan_bytes(
        tmp_path, "tests/path_fixture.md", invalid_prior_line.encode()
    )
    rejected_not_trailing = _scan_bytes(
        tmp_path, "tests/path_fixture.md", invalid_not_trailing.encode()
    )

    assert allowed.errors == 0
    assert "REPO-BUSINESS-ABSOLUTE-PATH" in _finding_ids(rejected_prior)
    assert "REPO-BUSINESS-ABSOLUTE-PATH" in _finding_ids(rejected_not_trailing)


@pytest.mark.parametrize("unc_path", _unc_samples())
def test_repository_hygiene_rejects_escaped_unc_source_and_json(tmp_path, unc_path):
    import json

    escaped_source = "source = " + repr(unc_path)
    declared_synthetic_json = json.dumps(
        {
            "fixture_kind": "synthetic",
            "contains_real_business_data": False,
            "path": unc_path,
        }
    )

    source_report = _scan_bytes(
        tmp_path,
        "tests/path_source.py",
        escaped_source.encode(),
    )
    json_report = _scan_bytes(
        tmp_path,
        "tests/fixtures/synthetic_unc.json",
        declared_synthetic_json.encode(),
    )

    assert "REPO-BUSINESS-ABSOLUTE-PATH" in _finding_ids(source_report)
    assert "REPO-BUSINESS-ABSOLUTE-PATH" in _finding_ids(json_report)


def test_scheme_urls_are_not_misclassified_as_forward_slash_unc(tmp_path):
    report = _scan_bytes(
        tmp_path,
        "docs/api.md",
        b"https://example.invalid/share/customer/contract.pdf",
    )

    assert report.errors == 0


@pytest.mark.parametrize(
    ("relative_path", "content"),
    [
        (
            "config/provider.py",
            "database"
            + "Pass"
            + "word = "
            + repr("-".join(("production", "value", "0123456789"))),
        ),
        (
            "config/provider.js",
            'const servicePassword = "production-value-0123456789";',
        ),
        (
            "config/provider.js",
            'let databasePasswd = "production-value-0123456789";',
        ),
        (
            "config/provider.js",
            'var adminPassword = "production-value-0123456789";',
        ),
        (
            "deploy/provider.yaml",
            "env:\n  - name: SERVICE_PASSWORD\n    value: production-value-0123456789\n",
        ),
    ],
)
def test_main_scanner_rejects_password_js_and_kubernetes_credentials(
    tmp_path, relative_path, content
):
    report = _scan_bytes(tmp_path, relative_path, content.encode())

    assert "REPO-HARDCODED-CREDENTIAL" in _finding_ids(report), content


@pytest.mark.parametrize(
    "content",
    [
        'const servicePassword = process.env.SERVICE_PASSWORD;',
        'let databasePasswd = "${DATABASE_PASSWD}";',
        "env:\n  - name: SERVICE_PASSWORD\n    value: ${SERVICE_PASSWORD}\n",
        'const cacheKey = "document-version-content-hash";',
    ],
)
def test_main_scanner_preserves_dynamic_js_yaml_and_benign_keys(tmp_path, content):
    report = _scan_bytes(tmp_path, "config/provider.yaml", content.encode())

    assert report.errors == 0, report.findings


@pytest.mark.parametrize(
    "encoded_json",
    [
        r'{"path":"\/\/nas01\/share\/customer\/contract.pdf"}',
        r'{"deep":{"items":[{"path":"\u005c\u005cnas01\u005cshare\u005ccustomer\u005ccontract.pdf"}]}}',
        r'{"paths":["\u005c\u005c?\u005cUNC/nas01\u005cshare/customer/contract.pdf"]}',
    ],
)
def test_main_scanner_rejects_decoded_json_unc_variants(tmp_path, encoded_json):
    report = _scan_bytes(
        tmp_path,
        "config/provider.json",
        encoded_json.encode(),
    )

    assert "REPO-BUSINESS-ABSOLUTE-PATH" in _finding_ids(report), encoded_json


def test_main_scanner_allows_decoded_json_relative_paths(tmp_path):
    encoded_json = r'{"deep":[{"path":"relative\/folder\/contract.pdf"}]}'

    report = _scan_bytes(tmp_path, "config/provider.json", encoded_json.encode())

    assert report.errors == 0, report.findings


@pytest.mark.parametrize(
    ("relative_path", "content"),
    [
        (
            "config/provider.js",
            'export const servicePassword = "production-value-0123456789";',
        ),
        (
            "config/provider.js",
            'const servicePassword: string = "production-value-0123456789";',
        ),
        (
            "deploy/provider.yaml",
            "env:\n  - value: production-value-0123456789\n    name: SERVICE_PASSWORD\n",
        ),
    ],
)
def test_main_scanner_rejects_export_typed_js_and_unordered_kubernetes_env(
    tmp_path, relative_path, content
):
    report = _scan_bytes(tmp_path, relative_path, content.encode())

    assert "REPO-HARDCODED-CREDENTIAL" in _finding_ids(report), content


def test_main_scanner_rejects_decoded_json_unc_object_key(tmp_path):
    escape = "\\" + "u005c"
    encoded_json = (
        '{"'
        + escape
        + escape
        + "nas01"
        + escape
        + "share"
        + escape
        + 'customer":1}'
    )

    report = _scan_bytes(tmp_path, "config/provider.json", encoded_json.encode())

    assert "REPO-BUSINESS-ABSOLUTE-PATH" in _finding_ids(report)
