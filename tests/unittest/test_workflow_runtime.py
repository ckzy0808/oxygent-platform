"""Tests for environment-backed Web workflow composition."""

import json

import pytest

from oxygent.platform import (
    MappingCredentialResolver,
    build_environment_workflow_bundle,
    environment_workflow_enabled,
)


def workflow_environment() -> dict[str, str]:
    values = {"OXYGENT_ENABLE_REAL_WORKFLOW": "1"}
    for prefix in ("PM", "ARCHITECT", "LEAD", "REVIEWER"):
        values.update(
            {
                f"OXYGENT_{prefix}_PROVIDER_TYPE": "openai-compatible",
                f"OXYGENT_{prefix}_BASE_URL": f"https://{prefix.lower()}.invalid/v1",
                f"OXYGENT_{prefix}_API_KEY": f"unit-secret-{prefix.lower()}",
                f"OXYGENT_{prefix}_MODEL": f"model-{prefix.lower()}",
            }
        )
    return values


def test_environment_bundle_builds_four_roles_without_serializing_credentials():
    environment = workflow_environment()
    resolver = MappingCredentialResolver(
        {
            f"env:OXYGENT_{prefix}_API_KEY": environment[
                f"OXYGENT_{prefix}_API_KEY"
            ]
            for prefix in ("PM", "ARCHITECT", "LEAD", "REVIEWER")
        }
    )
    bundle = build_environment_workflow_bundle(
        environment=environment,
        credential_resolver=resolver,
    )

    assert len(bundle.control_plane.agents.list()) == 4
    assert len(bundle.control_plane.models.list()) == 4
    assert len(bundle.oxy_space) == 6
    reviewer = bundle.control_plane.model_policies.for_role("reviewer")
    assert reviewer.exclude_same_provider_as_producer is True
    serialized = json.dumps(
        [
            item.model_dump(mode="json", by_alias=True)
            for item in bundle.control_plane.providers.list()
        ]
    )
    assert "unit-secret" not in serialized
    assert "env:OXYGENT_REVIEWER_API_KEY" in serialized


def test_reviewer_provider_exclusion_can_be_disabled_for_one_provider_demo():
    environment = workflow_environment()
    environment["OXYGENT_REVIEWER_EXCLUDE_PRODUCER_PROVIDER"] = "0"
    bundle = build_environment_workflow_bundle(environment=environment)
    reviewer = bundle.control_plane.model_policies.for_role("reviewer")
    assert reviewer.exclude_same_provider_as_producer is False


def test_shared_provider_configuration_is_reused_by_all_four_role_models():
    environment = {
        "OXYGENT_ENABLE_REAL_WORKFLOW": "1",
        "OXYGENT_SHARED_PROVIDER_ID": "shared-gpt",
        "OXYGENT_SHARED_PROVIDER_TYPE": "openai-compatible",
        "OXYGENT_SHARED_BASE_URL": "https://shared.invalid/v1",
        "OXYGENT_SHARED_CREDENTIAL_REFERENCE": "keychain:test-service/test-account",
        "OXYGENT_SHARED_MODEL": "shared-model",
        "OXYGENT_REVIEWER_EXCLUDE_PRODUCER_PROVIDER": "0",
    }
    bundle = build_environment_workflow_bundle(environment=environment)

    assert [item.id for item in bundle.control_plane.providers.list()] == [
        "shared-gpt"
    ]
    assert {
        model.provider_id for model in bundle.control_plane.models.list()
    } == {"shared-gpt"}
    assert len(bundle.control_plane.models.list()) == 4


def test_environment_bundle_requires_non_ollama_credentials():
    environment = workflow_environment()
    environment.pop("OXYGENT_PM_API_KEY")
    with pytest.raises(RuntimeError, match="OXYGENT_PM_API_KEY"):
        build_environment_workflow_bundle(environment=environment)


def test_real_workflow_enablement_is_explicit():
    assert environment_workflow_enabled({}) is False
    assert environment_workflow_enabled({"OXYGENT_ENABLE_REAL_WORKFLOW": "true"})
