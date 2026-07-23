import codecs
from pathlib import Path

import pytest


def _write_and_scan(tmp_path: Path, relative_path: str, payload: bytes):
    from governance.repository_hygiene import validate_repository_hygiene

    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return validate_repository_hygiene(
        project_root=tmp_path,
        tracked_files=[relative_path],
        verbose=False,
    )


def test_current_tracked_swift_source_is_managed_text():
    from governance.repository_hygiene import validate_repository_hygiene

    project_root = Path(__file__).resolve().parents[1]
    relative_path = "integrations/macos_vision_bridge/swift_ocr_bridge.swift"

    assert (project_root / relative_path).is_file()
    report = validate_repository_hygiene(
        project_root=project_root,
        tracked_files=[relative_path],
        verbose=False,
    )

    assert report.tracked_count == 1
    assert report.scanned_text_count == 1
    assert report.skipped_binary_count == 0
    assert report.skipped_non_target_count == 0


def test_current_repository_has_no_skipped_non_target_text():
    from governance.repository_hygiene import validate_repository_hygiene

    project_root = Path(__file__).resolve().parents[1]
    report = validate_repository_hygiene(project_root=project_root, verbose=False)

    assert report.skipped_non_target_count == 0


@pytest.mark.parametrize(
    ("bom", "encoding", "sensitive_text"),
    [
        (
            codecs.BOM_UTF32_LE,
            "utf-32-le",
            "/" + "Users/alice/Documents/private",
        ),
        (
            codecs.BOM_UTF32_BE,
            "utf-32-be",
            "https://192." + "168.10.8/v1",
        ),
    ],
)
def test_repository_hygiene_rejects_unsupported_utf32_before_utf16_decode(
    tmp_path, bom, encoding, sensitive_text
):
    payload = bom + sensitive_text.encode(encoding)
    report = _write_and_scan(tmp_path, "docs/unsupported.md", payload)

    assert report.errors == 1
    assert report.findings[0]["id"] == "REPO-TEXT-DECODE"
    assert report.decode_error_count == 1
    assert report.scanned_text_count == 0


def test_repository_hygiene_rejects_residual_nul_after_supported_decode(tmp_path):
    sensitive_text = ("/" + "home/alice/private").encode("utf-8")
    report = _write_and_scan(
        tmp_path,
        "docs/nul.md",
        b"portable-prefix\x00" + sensitive_text,
    )

    assert report.errors == 1
    assert report.findings[0]["id"] == "REPO-TEXT-DECODE"
    assert report.decode_error_count == 1
    assert report.scanned_text_count == 0


@pytest.mark.parametrize(
    "uri",
    [
        "file:///" + "Users/alice/Documents/private",
        "file:///" + "home/alice/Documents/private",
        "file:///" + "C:/" + "Users/alice/Documents/private",
        "file:///%" + "55sers/alice/Documents/private",
    ],
)
def test_repository_hygiene_detects_personal_paths_in_file_uris(tmp_path, uri):
    report = _write_and_scan(tmp_path, "docs/location.md", uri.encode("utf-8"))

    assert report.errors == 1
    assert report.findings[0]["id"] == "REPO-PERSONAL-PATH"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/" + "Users/alice/profile",
        "https://example.invalid/" + "home/alice/profile",
        "https://example.invalid/" + "C:/" + "Users/alice/profile",
    ],
)
def test_repository_hygiene_ignores_personal_looking_http_url_paths(tmp_path, url):
    report = _write_and_scan(tmp_path, "docs/link.md", url.encode("utf-8"))

    assert report.errors == 0
