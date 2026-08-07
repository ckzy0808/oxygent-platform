"""Tests for the runnable local Project and Code Workspace demo."""

from pathlib import Path

from examples.platform.projects_web_demo import (
    build_services,
    configure_default_model_fallback,
    configured_repository_roots,
)

import pytest


def test_code_workspace_defaults_to_the_current_oxygent_repository(monkeypatch):
    monkeypatch.delenv("OXYGENT_CODE_REPOSITORIES", raising=False)
    monkeypatch.delenv("OXYGENT_DEMO_REPOSITORY", raising=False)
    monkeypatch.delenv("OXYGENT_DISABLE_CODE_WORKSPACE", raising=False)

    roots = configured_repository_roots()

    assert roots == {"current-oxygent": Path(__file__).resolve().parents[2]}


def test_code_workspace_uses_an_explicit_allow_list(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setenv("OXYGENT_CODE_REPOSITORIES", f"{first}:{second}")

    roots = configured_repository_roots()

    assert roots == {
        "local-repository-1": first,
        "local-repository-2": second,
    }


def test_default_llm_is_reused_by_four_role_runtime_without_copying_secret(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("OXYGENT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEFAULT_LLM_API_KEY", "test-secret")
    monkeypatch.setenv("DEFAULT_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("DEFAULT_LLM_MODEL_NAME", "test-model")

    configure_default_model_fallback()

    assert __import__("os").environ["OXYGENT_ENABLE_REAL_WORKFLOW"] == "1"
    assert (
        __import__("os").environ["OXYGENT_SHARED_CREDENTIAL_REFERENCE"]
        == "env:DEFAULT_LLM_API_KEY"
    )
    assert (
        "test-secret"
        not in __import__("os").environ["OXYGENT_SHARED_CREDENTIAL_REFERENCE"]
    )


@pytest.mark.asyncio
async def test_real_workflow_does_not_seed_unrelated_demo_artifacts(monkeypatch):
    monkeypatch.setenv("OXYGENT_ENABLE_REAL_WORKFLOW", "1")
    monkeypatch.delenv("OXYGENT_SEED_DEMO_DATA", raising=False)
    monkeypatch.setenv("OXYGENT_DISABLE_CODE_WORKSPACE", "1")

    services = await build_services()
    projects = await services.projects.list()

    assert len(projects) == 1
    assert services.artifacts.list(projects[0].id) == []
