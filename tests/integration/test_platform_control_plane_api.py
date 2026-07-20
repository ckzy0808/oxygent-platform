"""API integration tests for Agents, Models, Providers, and routing policies."""

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from oxygent.platform import (
    AgentProfile,
    AgentProfileRegistry,
    HealthResult,
    HealthStatus,
    InMemoryExecutionTraceStore,
    InMemoryModelUsageStore,
    InvocationStatus,
    ModelProfile,
    ModelRegistry,
    ModelUsage,
    PlatformControlPlane,
    PlatformServices,
    ProviderAdapterRegistry,
    ProviderProfile,
    ProviderRegistry,
    ProviderType,
    RoleModelPolicy,
    RoleModelPolicyRegistry,
    RoleRegistry,
    RouteDecisionTrace,
    ToolPolicy,
    ToolPolicyRegistry,
    build_platform_router,
    default_role_definitions,
)


SECRET_VALUE = "super-secret-resolved-api-key"


class SensitiveHealthAdapter:
    async def health_check(self, _provider, _model):
        return HealthResult(
            status=HealthStatus.HEALTHY,
            latencyMs=24.0,
            reason=f"Authorization: Bearer {SECRET_VALUE}",
        )


def configured_services(*, allow_mutations: bool = True) -> PlatformServices:
    providers = ProviderRegistry(
        [
            ProviderProfile(
                id="provider-a",
                name="Provider A",
                providerType=ProviderType.OPENAI_COMPATIBLE,
                baseUrl=f"https://provider.invalid/v1?api_key={SECRET_VALUE}",
                credentialReference="env:PROVIDER_A_KEY",
                healthStatus="healthy",
            )
        ]
    )
    models = ModelRegistry(
        [
            ModelProfile(
                id="model-a",
                providerId="provider-a",
                modelName="model-a",
                displayName="Model A",
                capabilities={"text", "structured-output"},
                contextWindow=128000,
                healthStatus="healthy",
            )
        ]
    )
    policies = RoleModelPolicyRegistry(
        [
            RoleModelPolicy(
                id="pm-policy",
                roleId="product_manager",
                primaryModelIds=["model-a"],
                requiredCapabilities={"text"},
            )
        ]
    )
    agents = AgentProfileRegistry(
        [
            AgentProfile(
                id="pm-profile",
                name="Product Manager",
                agentName="pm-agent",
                roleId="product_manager",
                modelPolicyId="pm-policy",
                toolPolicyId="planning-tools",
                promptKey="platform.product_manager.v1",
            )
        ]
    )
    usage = InMemoryModelUsageStore()
    usage.append(
        ModelUsage(
            projectId="project",
            taskId="task",
            runId="run",
            roleId="product_manager",
            agentId="pm-profile",
            providerId="provider-a",
            modelId="model-a",
            inputTokens=100,
            outputTokens=50,
            latencyMs=400,
            estimatedCost=0.01,
            status=InvocationStatus.FAILED,
            failureReason=f"request failed with {SECRET_VALUE}",
        )
    )
    traces = InMemoryExecutionTraceStore()
    traces.append_route_decision(
        RouteDecisionTrace(
            id="route",
            projectId="project",
            taskId="task",
            runId="run",
            roleId="product_manager",
            agentId="pm-profile",
            taskType="requirements",
            selectedProviderId="provider-a",
            selectedModelId="model-a",
            selectionReason=f"priority; api_key={SECRET_VALUE}; capabilities matched",
        )
    )
    adapters = ProviderAdapterRegistry()
    adapters.register(ProviderType.OPENAI_COMPATIBLE, SensitiveHealthAdapter())
    control = PlatformControlPlane(
        providers=providers,
        models=models,
        roles=RoleRegistry(default_role_definitions()),
        agents=agents,
        model_policies=policies,
        tool_policies=ToolPolicyRegistry(
            [
                ToolPolicy(
                    id="planning-tools",
                    name="Planning tools",
                    allowedTools=["artifact-read"],
                )
            ]
        ),
        usage=usage,
        traces=traces,
        adapters=adapters,
        allow_provider_mutations=allow_mutations,
    )
    return PlatformServices(control_plane=control)


def build_app(services: PlatformServices) -> FastAPI:
    app = FastAPI()
    app.include_router(build_platform_router(services))
    return app


@pytest.mark.asyncio
async def test_agent_model_policy_and_usage_views_are_enriched_and_redacted():
    app = build_app(configured_services())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        agents = (await client.get("/api/v1/platform/agents")).json()["data"]
        providers = (await client.get("/api/v1/platform/providers")).json()["data"]
        models = (await client.get("/api/v1/platform/models")).json()["data"]
        policies = (await client.get("/api/v1/platform/routing-policies")).json()[
            "data"
        ]
        usage = (await client.get("/api/v1/platform/usage")).json()["data"]

    agent = agents["items"][0]
    assert agent["role"]["name"] == "Product Manager"
    assert agent["provider"]["name"] == "Provider A"
    assert agent["model"]["displayName"] == "Model A"
    assert agent["routingState"] == "Fixed"
    assert agent["toolPolicy"]["allowedTools"] == ["artifact-read"]
    assert "[redacted]" in agent["selectionReason"]
    assert models["items"][0]["assignedRoles"] == ["Product Manager"]
    assert policies["items"][0]["primaryModels"][0]["id"] == "model-a"
    assert providers["items"][0]["credentialMask"] == "env:••••••••"
    assert providers["items"][0]["credentialReference"] == "env:PROVIDER_A_KEY"
    assert providers["items"][0]["baseUrl"] == "https://provider.invalid/v1"
    assert usage["items"][0]["failureReason"] == "Provider call failed"
    assert SECRET_VALUE not in json.dumps(
        {"agents": agents, "providers": providers, "usage": usage}
    )


@pytest.mark.asyncio
async def test_connection_health_response_omits_adapter_reason_and_secret():
    app = build_app(configured_services())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/platform/providers/provider-a/test-connection",
            json={"modelId": "model-a"},
        )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["health"] == {
        "status": "healthy",
        "latencyMs": 24.0,
        "message": "Connection succeeded",
    }
    assert SECRET_VALUE not in response.text
    assert "reason" not in payload["health"]


@pytest.mark.asyncio
async def test_provider_mutation_gate_and_reference_validation():
    readonly_app = build_app(configured_services(allow_mutations=False))
    async with AsyncClient(
        transport=ASGITransport(app=readonly_app), base_url="http://test"
    ) as client:
        blocked = await client.patch(
            "/api/v1/platform/providers/provider-a", json={"enabled": False}
        )
        assert blocked.status_code == 403

    mutable_app = build_app(configured_services())
    async with AsyncClient(
        transport=ASGITransport(app=mutable_app), base_url="http://test"
    ) as client:
        raw_secret = await client.post(
            "/api/v1/platform/providers",
            json={
                "id": "provider-b",
                "name": "Provider B",
                "providerType": "gemini",
                "baseUrl": "https://provider-b.invalid",
                "credentialReference": SECRET_VALUE,
            },
        )
        assert raw_secret.status_code == 422
        assert SECRET_VALUE not in raw_secret.text
