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


@pytest.mark.parametrize(
    "url_builder",
    [
        lambda host: f"http://user@{host}/v1/chat",
        lambda host: f"https://user:password@{host}:9443/v1/chat?token=secret",
        lambda host: f"http://{host}:8080/path",
    ],
)
def test_repository_hygiene_parses_url_hostname_before_rfc1918_check(
    tmp_path, url_builder
):
    host = "172." + "20.1.2"
    report = _write_and_scan(
        tmp_path,
        "config/provider.yaml",
        url_builder(host).encode("utf-8"),
    )

    assert report.errors == 1
    assert report.findings[0]["id"] == "REPO-RFC1918-URL"


@pytest.mark.parametrize(
    "host",
    [
        "172." + "15.1.2",
        "172." + "32.1.2",
        "192." + "169.1.2",
        "172." + "999.1.2",
    ],
)
def test_repository_hygiene_does_not_guess_non_rfc1918_hosts(tmp_path, host):
    report = _write_and_scan(
        tmp_path,
        "config/provider.yaml",
        f"https://user:password@{host}:9443/v1".encode("utf-8"),
    )

    assert report.errors == 0


@pytest.mark.parametrize(
    "sensitive_text",
    [
        "/Users/" + "alice/Documents/private",
        "https://user:password@192." + "168.10.8:9443/v1",
    ],
)
def test_repository_hygiene_scans_utf16_bom_text(tmp_path, sensitive_text):
    report = _write_and_scan(
        tmp_path,
        "docs/portable.md",
        sensitive_text.encode("utf-16"),
    )

    assert report.errors == 1
    assert report.scanned_text_count == 1
    assert report.skipped_binary_count == 0


def test_repository_hygiene_fails_closed_for_undecodable_managed_text(tmp_path):
    report = _write_and_scan(tmp_path, "docs/broken.md", bytes([255]))

    assert report.errors == 1
    assert report.findings[0]["id"] == "REPO-TEXT-DECODE"
    assert report.decode_error_count == 1


def test_repository_hygiene_default_denies_binary_and_unknown_file_types(tmp_path):
    from governance.repository_hygiene import validate_repository_hygiene

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("portable", encoding="utf-8-sig")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")
    (tmp_path / "NOTICE.unknown").write_text("not a managed role", encoding="utf-8")

    report = validate_repository_hygiene(
        project_root=tmp_path,
        tracked_files=["docs/readme.md", "image.png", "NOTICE.unknown"],
        verbose=False,
    )

    assert report.tracked_count == 3
    assert report.scanned_text_count == 1
    assert report.skipped_binary_count == 0
    assert report.skipped_non_target_count == 0
    assert report.errors == 2
    assert {finding["id"] for finding in report.findings} == {
        "REPO-TRACKED-SENSITIVE-FILE",
        "REPO-TRACKED-UNSUPPORTED-FILE",
    }
