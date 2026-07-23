import os
from unittest.mock import patch

import pytest


def test_project_manager_base_url_has_priority_over_legacy_environment():
    from common.provider_config import resolve_llm_base_url

    assert resolve_llm_base_url(
        environ={
            "PROJECT_MANAGER_LLM_BASE_URL": "https://llm.example.invalid/v1",
            "LLM_BASE_URL": "https://legacy-one.example.invalid/v1",
            "OPENAI_API_BASE": "https://legacy-two.example.invalid/v1",
        },
        required=True,
    ) == "https://llm.example.invalid/v1"


@pytest.mark.parametrize("legacy_name", ["LLM_BASE_URL", "OPENAI_API_BASE"])
def test_legacy_base_url_environment_is_an_explicit_fallback(legacy_name):
    from common.provider_config import resolve_llm_base_url

    assert resolve_llm_base_url(
        environ={legacy_name: "https://legacy.example.invalid/v1"},
        required=True,
    ) == "https://legacy.example.invalid/v1"


def test_missing_base_url_is_optional_until_llm_is_enabled():
    from common.provider_config import ProviderConfigurationError, resolve_llm_base_url

    assert resolve_llm_base_url(environ={}) is None
    with pytest.raises(
        ProviderConfigurationError,
        match="PROJECT_MANAGER_LLM_BASE_URL is required when LLM is enabled",
    ):
        resolve_llm_base_url(environ={}, required=True)


def test_main_llm_adapter_requires_centralized_base_url(monkeypatch):
    from common.provider_config import ProviderConfigurationError
    import main

    for name in (
        "PROJECT_MANAGER_LLM_BASE_URL",
        "LLM_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    with pytest.raises(ProviderConfigurationError):
        main._make_api_llm()


def test_document_extractor_requires_url_only_when_constructed_for_llm():
    from common.provider_config import ProviderConfigurationError
    from skills.document_parse.llm_extractor import make_llm_extractor

    with patch.dict(os.environ, {"LLM_API_KEY": "test-key"}, clear=True):
        with pytest.raises(ProviderConfigurationError):
            make_llm_extractor()


def test_archive_llm_fallback_reports_stable_missing_url(monkeypatch):
    from skills.archive_files.scripts import build_archive_decision as archive

    for name in (
        "PROJECT_MANAGER_LLM_BASE_URL",
        "LLM_BASE_URL",
        "OPENAI_API_BASE",
    ):
        monkeypatch.delenv(name, raising=False)

    result = archive._llm_general_fallback("proposal.pdf", None)

    assert result["llm_fallback_used"] is False
    assert result["llm_error"] == (
        "PROJECT_MANAGER_LLM_BASE_URL is required when LLM is enabled"
    )
