"""Control-plane policy, mutation, health, and credential safety tests."""

import pytest

from oxygent.platform import (
    AgentProfile,
    AgentProfileRegistry,
    HealthResult,
    HealthStatus,
    InvocationStatus,
    ModelProfile,
    ModelRegistry,
    ModelUsage,
    PlatformControlPlane,
    ProviderAdapterRegistry,
    ProviderCreate,
    ProviderProfile,
    ProviderRegistry,
    ProviderType,
    RoleModelPolicy,
    RoleModelPolicyRegistry,
    RoleRegistry,
    ToolPolicy,
    ToolPolicyRegistry,
    default_role_definitions,
)


class HealthyAdapter:
    async def health_check(self, _provider, _model):
        return HealthResult(
            status=HealthStatus.HEALTHY,
            latencyMs=12.5,
            reason="sensitive adapter detail must not be used by API callers",
        )


def control_plane(*, allow_mutations: bool = True) -> PlatformControlPlane:
    provider = ProviderProfile(
        id="provider-a",
        name="Provider A",
        providerType=ProviderType.OPENAI_COMPATIBLE,
        baseUrl="https://provider.invalid/v1",
        credentialReference="env:PROVIDER_A_KEY",
    )
    model = ModelProfile(
        id="model-a",
        providerId=provider.id,
        modelName="model-a",
        displayName="Model A",
        capabilities={"text"},
    )
    policy = RoleModelPolicy(
        id="pm-policy",
        roleId="product_manager",
        primaryModelIds=[model.id],
        requiredCapabilities={"text"},
    )
    profile = AgentProfile(
        id="pm-profile",
        name="Product Manager",
        agentName="pm-agent",
        roleId="product_manager",
        modelPolicyId=policy.id,
        toolPolicyId="planning-tools",
        promptKey="platform.product_manager.v1",
    )
    adapters = ProviderAdapterRegistry()
    adapters.register(ProviderType.OPENAI_COMPATIBLE, HealthyAdapter())
    return PlatformControlPlane(
        providers=ProviderRegistry([provider]),
        models=ModelRegistry([model]),
        roles=RoleRegistry(default_role_definitions()),
        agents=AgentProfileRegistry([profile]),
        model_policies=RoleModelPolicyRegistry([policy]),
        tool_policies=ToolPolicyRegistry(
            [ToolPolicy(id="planning-tools", name="Planning tools")]
        ),
        adapters=adapters,
        allow_provider_mutations=allow_mutations,
    )


def test_provider_mutation_requires_secret_reference_not_raw_key():
    control = PlatformControlPlane(allow_provider_mutations=True)
    with pytest.raises(ValueError, match="credentialReference"):
        control.create_provider(
            ProviderCreate(
                id="unsafe",
                name="Unsafe",
                providerType="openai-compatible",
                baseUrl="https://provider.invalid/v1",
                credentialReference="raw-secret-value",
            )
        )


def test_provider_mutations_are_disabled_by_default():
    control = control_plane(allow_mutations=False)
    with pytest.raises(PermissionError, match="disabled"):
        control.create_provider(
            ProviderCreate(
                id="provider-b",
                name="Provider B",
                providerType="gemini",
                baseUrl="https://provider-b.invalid",
                credentialReference="env:PROVIDER_B_KEY",
            )
        )


@pytest.mark.asyncio
async def test_health_check_updates_provider_and_model_without_changing_reference():
    control = control_plane()
    provider, model, result = await control.health_check("provider-a", "model-a")

    assert result.status is HealthStatus.HEALTHY
    assert provider.health_status is HealthStatus.HEALTHY
    assert model.health_status is HealthStatus.HEALTHY
    assert provider.credential_reference == "env:PROVIDER_A_KEY"


def test_routing_state_and_usage_summary_come_from_policy_and_invocations():
    control = control_plane()
    profile = control.agents.get("pm-profile")
    policy = control.model_policies.get("pm-policy")
    assert control.routing_state(profile, policy) == "Fixed"

    control.usage.append(
        ModelUsage(
            projectId="project",
            taskId="task",
            runId="run",
            roleId="product_manager",
            agentId=profile.id,
            providerId="provider-a",
            modelId="model-a",
            inputTokens=100,
            outputTokens=50,
            estimatedCost=0.02,
            status=InvocationStatus.SUCCEEDED,
            fallbackUsed=True,
        )
    )

    assert control.routing_state(profile, policy) == "Fallback"
    assert control.usage_summary(profile) == {
        "inputTokens": 100,
        "outputTokens": 50,
        "totalTokens": 150,
        "exactInvocations": 1,
        "estimatedInvocations": 0,
        "successRate": 1.0,
        "invocations": 1,
    }
