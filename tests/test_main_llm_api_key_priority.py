import pytest


@pytest.mark.parametrize(
    ("api_key", "base_url", "expected_exception"),
    [
        (None, None, RuntimeError),
        (None, "https://llm.example.invalid/v1", RuntimeError),
        ("test-key", None, "provider_configuration"),
        ("test-key", "https://llm.example.invalid/v1", None),
    ],
)
def test_main_llm_adapter_validates_api_key_before_endpoint(
    monkeypatch, api_key, base_url, expected_exception
):
    from common.provider_config import ProviderConfigurationError
    import main

    for name in (
        "PROJECT_MANAGER_LLM_BASE_URL",
        "LLM_BASE_URL",
        "OPENAI_API_BASE",
        "LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    if api_key:
        monkeypatch.setenv("LLM_API_KEY", api_key)
    if base_url:
        monkeypatch.setenv("PROJECT_MANAGER_LLM_BASE_URL", base_url)

    if expected_exception is RuntimeError:
        with pytest.raises(RuntimeError, match="LLM_API_KEY"):
            main._make_api_llm()
    elif expected_exception == "provider_configuration":
        with pytest.raises(ProviderConfigurationError):
            main._make_api_llm()
    else:
        assert main._make_api_llm().api_key == api_key
