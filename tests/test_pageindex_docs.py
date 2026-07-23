from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


def test_pageindex_docs_use_explicit_portable_configuration():
    documents = [
        PROJECT_DIR / "integrations" / "pageindex" / "README.md",
        PROJECT_DIR / "integrations" / "pageindex" / "SKILL.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in documents)

    assert "PROJECT_MANAGER_PAGEINDEX_DIR" in text
    assert "PageIndexClient(configured_dir)" in text
    assert "PageIndexClient()" not in text
    assert "/Users/" not in text
    assert "172.18.125.202" not in text
