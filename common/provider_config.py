"""Centralized, opt-in configuration for LLM provider endpoints."""
from __future__ import annotations

import os
from typing import Mapping, Optional


LLM_BASE_URL_ENV = "PROJECT_MANAGER_LLM_BASE_URL"
LLM_API_KEY_ENV = "LLM_API_KEY"
LLM_MODEL_ENV = "LLM_MODEL"
LEGACY_LLM_BASE_URL_ENVS = ("LLM_BASE_URL", "OPENAI_API_BASE")
MISSING_LLM_BASE_URL_MESSAGE = (
    "PROJECT_MANAGER_LLM_BASE_URL is required when LLM is enabled"
)
MISSING_LLM_API_KEY_MESSAGE = "LLM_API_KEY is required when LLM is enabled"
DEFAULT_LLM_MODEL = "minimax-m3-mxfp8"


class ProviderConfigurationError(RuntimeError):
    """Raised when an enabled external provider lacks required configuration."""


def resolve_llm_base_url(
    explicit: Optional[str] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    required: bool = False,
) -> Optional[str]:
    """Resolve the provider URL without inventing a network endpoint.

    Priority is explicit argument, the project-wide environment variable, then
    the two documented legacy variables. Missing configuration is harmless until
    a caller explicitly enables or invokes an LLM-backed capability.
    """
    environment = os.environ if environ is None else environ
    candidates = [
        explicit,
        environment.get(LLM_BASE_URL_ENV),
        *(environment.get(name) for name in LEGACY_LLM_BASE_URL_ENVS),
    ]
    for candidate in candidates:
        if candidate is not None and candidate.strip():
            return candidate.strip().rstrip("/")
    if required:
        raise ProviderConfigurationError(MISSING_LLM_BASE_URL_MESSAGE)
    return None


def resolve_llm_api_key(
    explicit: Optional[str] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    required: bool = False,
) -> Optional[str]:
    """Resolve the shared LLM credential without logging or transforming it."""
    environment = os.environ if environ is None else environ
    value = explicit if explicit is not None else environment.get(LLM_API_KEY_ENV)
    if value is not None and value.strip():
        return value.strip()
    if required:
        raise ProviderConfigurationError(MISSING_LLM_API_KEY_MESSAGE)
    return None


def resolve_llm_model(
    explicit: Optional[str] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the shared model name with the existing document-parse default."""
    environment = os.environ if environ is None else environ
    value = explicit if explicit is not None else environment.get(LLM_MODEL_ENV)
    return value.strip() if value is not None and value.strip() else DEFAULT_LLM_MODEL
