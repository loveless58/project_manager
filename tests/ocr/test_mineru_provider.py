from __future__ import annotations

from pathlib import Path
import subprocess

from ocr.providers.mineru_provider import MineruProvider


def test_mineru_provider_returns_blocked_when_command_is_not_declared(tmp_path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"synthetic scan")

    result = MineruProvider(command_resolver=lambda: None).extract(str(source))

    assert result["status"] == "blocked"
    assert result["engine"] == "mineru"
    assert result["blocked_reason"] == "ocr_provider_unavailable"


def test_mineru_provider_normalizes_local_markdown_output(tmp_path) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"synthetic scan")

    def runner(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        output_dir = Path(command[command.index("-o") + 1])
        output_dir.mkdir(parents=True)
        (output_dir / "result.md").write_text("# 合同\n\n合同编号 CT-001", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    result = MineruProvider(command_resolver=lambda: "mineru", runner=runner).extract(str(source))

    assert result["status"] == "success"
    assert result["engine"] == "mineru"
    assert result["text"] == "# 合同\n\n合同编号 CT-001"
    assert result["pages"][0]["page"] == 1
    assert result["pages"][0]["text"] == result["text"]
