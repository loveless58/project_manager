"""Centralized, opt-in configuration for LLM provider endpoints."""
from __future__ import annotations

import os
from typing import Mapping, Optional


LLM_BASE_URL_ENV = "PROJECT_MANAGER_LLM_BASE_URL"
LEGACY_LLM_BASE_URL_ENVS = ("LLM_BASE_URL", "OPENAI_API_BASE")
MISSING_LLM_BASE_URL_MESSAGE = (
    "PROJECT_MANAGER_LLM_BASE_URL is required when LLM is enabled"
)


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
