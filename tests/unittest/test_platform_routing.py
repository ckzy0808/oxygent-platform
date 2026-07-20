"""Tests for role/model registries, deterministic routing, usage, and secrecy."""

import logging

import pytest
from pydantic import ValidationError

from oxygent.oxy.agents.chat_agent import ChatAgent
from oxygent.platform import (
    AgentProfile,
    AgentProfileRegistry,
    HealthResult,
    HealthStatus,
    InMemoryArtifactStore,
    InMemoryExecutionTraceStore,
    InMemoryModelUsageStore,
    MappingCredentialResolver,
    ModelProfile,
    ModelRegistry,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ProviderAdapterRegistry,
    ProviderProfile,
    ProviderRegistry,
    ProviderType,
    RequirementSpec,
    RequirementSpecContent,
    RoleModelPolicy,
    RoleModelPolicyRegistry,
    RoleRegistry,
    RoutingContext,
    TaskGraphContent,
    ToolPolicy,
    ValidationStatus,
    default_role_definitions,
)
from oxygent.schemas import OxyRequest, OxyState


class FakeAdapter:
    def __init__(self, failing_models=(), secret: str = ""):
        self.failing_models = set(failing_models)
        self.secret = secret
        self.calls: list[str] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request.model.id)
        if request.model.id in self.failing_models:
            raise RuntimeError(self.secret or "provider unavailable")
        return ModelResponse(
            output=f"response-from-{request.model.id}",
            input_tokens=11,
            output_tokens=7,
            latency_ms=5,
        )

    async def stream(self, request):
        response = await self.complete(request)
        yield response

    async def health_check(self, provider, model):
        return HealthResult(status=HealthStatus.HEALTHY)


def build_router(*, adapter=None, reviewer_primary="model_b"):
    providers = ProviderRegistry(
        [
            ProviderProfile(
                id="provider_a",
                name="Provider A",
                providerType="openai-compatible",
                baseUrl="https://provider-a.invalid/v1",
                credentialReference="provider-a-key",
                healthStatus="healthy",
            ),
            ProviderProfile(
                id="provider_b",
                name="Provider B",
                providerType="openai-compatible",
                baseUrl="https://provider-b.invalid/v1",
                credentialReference="provider-b-key",
                healthStatus="healthy",
            ),
        ]
    )
    models = ModelRegistry(
        [
            ModelProfile(
                id="model_a",
                providerId="provider_a",
                modelName="model-a",
                displayName="Model A",
                capabilities={"text", "reasoning"},
                healthStatus="healthy",
            ),
            ModelProfile(
                id="model_b",
                providerId="provider_b",
                modelName="model-b",
                displayName="Model B",
                capabilities={"text", "reasoning"},
                healthStatus="healthy",
            ),
        ]
    )
    roles = RoleRegistry(default_role_definitions())
    policies = RoleModelPolicyRegistry(
        [
            RoleModelPolicy(
                id="pm_policy",
                roleId="product_manager",
                primaryModelIds=["model_a"],
                fallbackModelIds=["model_b"],
                requiredCapabilities={"text"},
            ),
            RoleModelPolicy(
                id="architect_policy",
                roleId="solution_architect",
                primaryModelIds=["model_b"],
                fallbackModelIds=["model_a"],
                requiredCapabilities={"text"},
            ),
            RoleModelPolicy(
                id="reviewer_policy",
                roleId="reviewer",
                primaryModelIds=[reviewer_primary],
                fallbackModelIds=[
                    "model_a" if reviewer_primary == "model_b" else "model_b"
                ],
                requiredCapabilities={"text"},
                excludeSameProviderAsProducer=True,
            ),
        ]
    )
    agents = AgentProfileRegistry(
        [
            AgentProfile(
                id="pm_agent_profile",
                name="PM Agent",
                agentName="pm_agent",
                roleId="product_manager",
                modelPolicyId="pm_policy",
                toolPolicyId="no_tools",
                promptKey="pm_prompt",
            ),
            AgentProfile(
                id="architect_agent_profile",
                name="Architect Agent",
                agentName="architect_agent",
                roleId="solution_architect",
                modelPolicyId="architect_policy",
                toolPolicyId="no_tools",
                promptKey="architect_prompt",
            ),
        ]
    )
    usage = InMemoryModelUsageStore()
    traces = InMemoryExecutionTraceStore()
    adapter = adapter or FakeAdapter()
    adapters = ProviderAdapterRegistry()
    adapters.register(ProviderType.OPENAI_COMPATIBLE, adapter)
    router = ModelRouter(
        name="model_router",
        provider_registry=providers,
        model_registry=models,
        role_registry=roles,
        policy_registry=policies,
        agent_profile_registry=agents,
        adapter_registry=adapters,
        usage_store=usage,
        trace_store=traces,
    )
    return router, adapter, usage, traces, agents, providers, models, policies, roles


@pytest.mark.asyncio
async def test_different_agents_use_different_models(dummy_mas):
    router, adapter, usage, _traces, _agents, *_rest = build_router()
    pm_agent = ChatAgent(name="pm_agent", llm_model="model_router")
    architect_agent = ChatAgent(name="architect_agent", llm_model="model_router")
    for oxy in (router, pm_agent, architect_agent):
        oxy.set_mas(dummy_mas)
        dummy_mas.add_oxy(oxy)

    pm_response = await pm_agent._execute(
        OxyRequest(
            mas=dummy_mas,
            callee="pm_agent",
            callee_category="agent",
            arguments={
                "query": "idea",
                "short_memory": [],
                "llm_params": {
                    "_routing_context": {
                        "roleId": "product_manager",
                        "agentId": "pm_agent_profile",
                    }
                },
            },
        )
    )
    architect_response = await architect_agent._execute(
        OxyRequest(
            mas=dummy_mas,
            callee="architect_agent",
            callee_category="agent",
            arguments={
                "query": "requirements",
                "short_memory": [],
                "llm_params": {
                    "_routing_context": {
                        "roleId": "solution_architect",
                        "agentId": "architect_agent_profile",
                    }
                },
            },
        )
    )

    assert pm_response.output == "response-from-model_a"
    assert architect_response.output == "response-from-model_b"
    assert adapter.calls == ["model_a", "model_b"]
    assert [record.model_id for record in usage.list()] == ["model_a", "model_b"]


@pytest.mark.asyncio
async def test_provider_failure_uses_fallback():
    router, adapter, usage, traces, *_rest = build_router(
        adapter=FakeAdapter(failing_models={"model_a"})
    )
    response = await router._execute(
        OxyRequest(
            arguments={
                "messages": [{"role": "user", "content": "hello"}],
                "_routing_context": {
                    "roleId": "product_manager",
                    "policyId": "pm_policy",
                },
            }
        )
    )

    assert response.state is OxyState.COMPLETED
    assert response.output == "response-from-model_b"
    assert response.extra["fallback_used"] is True
    assert adapter.calls == ["model_a", "model_b"]
    assert [record.status.value for record in usage.list()] == ["failed", "succeeded"]
    assert len(traces.route_decisions()) == 1


def test_reviewer_excludes_producer_provider():
    router, _adapter, _usage, _traces, _agents, *_rest = build_router(
        reviewer_primary="model_a"
    )
    role = router.role_registry.get("reviewer")
    policy = router.policy_registry.get("reviewer_policy")
    from oxygent.platform import ModelRoutingEngine

    decision = ModelRoutingEngine(
        router.provider_registry, router.model_registry, router.usage_store
    ).route(
        role,
        policy,
        RoutingContext(
            roleId="reviewer",
            producerProviderId="provider_a",
        ),
    )
    assert decision.provider_id == "provider_b"
    assert decision.model_profile_id == "model_b"


def test_artifact_schema_validation_and_append_only_revision():
    store = InMemoryArtifactStore()
    artifact = RequirementSpec(
        projectId="project",
        taskId="task",
        producerRole="product_manager",
        producerAgent="pm_agent",
        providerId="provider_a",
        modelId="model_a",
        content=RequirementSpecContent(summary="Initial requirements"),
        validationStatus=ValidationStatus.VALID,
    )
    store.append(artifact)

    with pytest.raises(ValidationError):
        RequirementSpec(
            projectId="project",
            taskId="task",
            producerRole="product_manager",
            producerAgent="pm_agent",
            providerId="provider_a",
            modelId="model_a",
            content=TaskGraphContent(summary="wrong schema"),
        )

    revision = store.revise(
        artifact.id,
        RequirementSpecContent(summary="Revised requirements"),
    )
    assert revision.id != artifact.id
    assert revision.revision == 2
    assert revision.supersedes_artifact_id == artifact.id
    assert store.get(artifact.id).content.summary == "Initial requirements"


@pytest.mark.asyncio
async def test_api_key_never_appears_in_logs(caplog):
    secret = "super-secret-api-key-value"
    resolver = MappingCredentialResolver({"provider-a-key": secret})
    assert secret not in repr(resolver)
    adapter = FakeAdapter(failing_models={"model_a", "model_b"}, secret=secret)
    router, *_rest = build_router(adapter=adapter)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(Exception):
            await router._execute(
                OxyRequest(
                    arguments={
                        "messages": [{"role": "user", "content": "hello"}],
                        "_routing_context": {
                            "roleId": "product_manager",
                            "policyId": "pm_policy",
                        },
                    }
                )
            )
    assert secret not in caplog.text
    assert secret not in router.model_dump_json()


def test_profiles_use_credential_reference_not_api_key():
    profile = ProviderProfile(
        id="provider",
        name="Provider",
        providerType="ollama",
        baseUrl="http://127.0.0.1:11434",
        credentialReference="env:OLLAMA_TOKEN",
    )
    dumped = profile.model_dump(by_alias=True)
    assert dumped["credentialReference"] == "env:OLLAMA_TOKEN"
    assert "apiKey" not in dumped
    assert "api_key" not in dumped


def test_tool_policy_rejects_conflicting_rules():
    with pytest.raises(ValidationError):
        ToolPolicy(
            id="policy",
            name="bad policy",
            allowedTools=["shell"],
            deniedTools=["shell"],
        )
