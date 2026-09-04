"""Tests for provider selection in the shared LLM client factory."""

import pytest

from src.agents import llm_client


@pytest.fixture(autouse=True)
def _clear_llm_env(monkeypatch):
    for var in (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "VERTEX_PROJECT_ID",
        "VERTEX_LOCATION",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_defaults_to_vertex_thinking_model():
    assert llm_client.get_model() == "google/gemini-2.5-pro"


def test_model_override_wins(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "google/gemini-2.5-flash")
    assert llm_client.get_model() == "google/gemini-2.5-flash"


def test_model_default_per_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert llm_client.get_model() == "gpt-4o"
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert llm_client.get_model() == "gemini-2.5-flash"


def test_vertex_requires_project(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "vertex")
    with pytest.raises(llm_client.LLMConfigError):
        llm_client.get_client()


def test_vertex_base_url_uses_project_and_location(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "vertex")
    monkeypatch.setenv("VERTEX_PROJECT_ID", "proj-1")
    monkeypatch.setenv("VERTEX_LOCATION", "us-central1")
    monkeypatch.setattr(llm_client, "_vertex_access_token", lambda: "token")

    client = llm_client.get_client()

    assert "projects/proj-1" in str(client.base_url)
    assert "locations/us-central1" in str(client.base_url)


def test_gemini_uses_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    client = llm_client.get_client()

    assert client.api_key == "test-key"
    assert "generativelanguage" in str(client.base_url)
