"""Central LLM client factory.

All agents talk to the LLM through this module so the provider and model
are configured in exactly one place. The default provider is Vertex AI,
accessed through its OpenAI-compatible endpoint, which lets the existing
``AsyncOpenAI`` call sites keep working unchanged.

Vertex authenticates with a short-lived OAuth access token rather than a
static API key, so the token is refreshed from Application Default
Credentials on each client construction.

Environment variables:
    LLM_PROVIDER        "vertex" (default), "gemini", or "openai"
    LLM_MODEL           model id override (defaults per provider)
    VERTEX_PROJECT_ID   GCP project for Vertex (required for vertex)
    VERTEX_LOCATION     Vertex region (default "global")
    GEMINI_API_KEY      required when provider is gemini
    OPENAI_API_KEY      required when provider is openai
"""

from __future__ import annotations

import os
import subprocess

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
# Gemini 2.5 models reason ("think") before answering by default.
_DEFAULT_VERTEX_MODEL = "google/gemini-2.5-pro"
_DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
_DEFAULT_OPENAI_MODEL = "gpt-4o"


class LLMConfigError(RuntimeError):
    """Raised when the configured provider is missing required settings."""


def _provider() -> str:
    return os.getenv("LLM_PROVIDER", "vertex").strip().lower()


def get_model() -> str:
    """Model id to pass to ``chat.completions.create``."""
    explicit = os.getenv("LLM_MODEL")
    if explicit:
        return explicit
    provider = _provider()
    if provider == "vertex":
        return _DEFAULT_VERTEX_MODEL
    if provider == "gemini":
        return _DEFAULT_GEMINI_MODEL
    return _DEFAULT_OPENAI_MODEL


def _vertex_access_token() -> str:
    """Fetch a fresh OAuth token from Application Default Credentials."""
    try:
        from google.auth import default  # type: ignore
        from google.auth.transport.requests import Request  # type: ignore

        credentials, _ = default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        credentials.refresh(Request())
        if credentials.token:
            return credentials.token
    except Exception:  # noqa: BLE001 - fall back to the gcloud CLI
        pass

    try:
        return subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        raise LLMConfigError(
            "Could not obtain a Vertex AI access token. Run "
            "'gcloud auth application-default login'."
        ) from exc


def _vertex_base_url() -> str:
    project = os.getenv("VERTEX_PROJECT_ID", "").strip()
    if not project:
        raise LLMConfigError("VERTEX_PROJECT_ID is not set.")
    location = os.getenv("VERTEX_LOCATION", "global").strip() or "global"
    return (
        f"https://aiplatform.googleapis.com/v1/projects/{project}"
        f"/locations/{location}/endpoints/openapi"
    )


def tool_name(action: str = "chat") -> str:
    """Provider-qualified tool name for the event log, e.g. "vertex.chat"."""
    return f"{_provider()}.{action}"


def thinking_extra_body() -> dict:
    """Provider options that stream the model's reasoning, if supported.

    Gemini 2.5 models reason before answering. Without this the reasoning
    is billed but never sent, so the UI shows nothing during the pause
    before the answer arrives.
    """
    if _provider() in ("vertex", "gemini"):
        return {"google": {"thinking_config": {"include_thoughts": True}}}
    return {}


# A review makes many calls in quick succession, so a transient 429 from
# the provider is normal and must not end the run. The SDK retries these
# with exponential backoff when given a budget.
_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "5"))
_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "180"))


def get_client():
    """An ``AsyncOpenAI`` client pointed at the configured provider."""
    from openai import AsyncOpenAI

    provider = _provider()
    common = {"max_retries": _MAX_RETRIES, "timeout": _TIMEOUT_SECONDS}

    if provider == "vertex":
        return AsyncOpenAI(
            api_key=_vertex_access_token(),
            base_url=_vertex_base_url(),
            **common,
        )

    if provider == "gemini":
        return AsyncOpenAI(
            api_key=os.getenv("GEMINI_API_KEY", ""),
            base_url=os.getenv("GEMINI_BASE_URL", _GEMINI_BASE_URL),
            **common,
        )

    return AsyncOpenAI(**common)
