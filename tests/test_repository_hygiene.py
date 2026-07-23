from pathlib import Path


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


def test_current_tracked_repository_passes_hygiene_scan():
    from governance.validate import validate_repository_hygiene

    project_root = Path(__file__).resolve().parents[1]
    errors, warnings, findings = validate_repository_hygiene(
        project_root=project_root,
        verbose=False,
    )

    assert (errors, warnings, findings) == (0, 0, [])
