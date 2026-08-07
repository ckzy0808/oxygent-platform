"""End-to-end integration tests for the four-role Artifact workflow."""

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from oxygent import MAS
from oxygent.oxy import ChatAgent, MockLLM
from oxygent.platform import (
    AgentProfile,
    AgentProfileRegistry,
    BasicRoleWorkflow,
    HealthResult,
    HealthStatus,
    InMemoryArtifactStore,
    InMemoryExecutionTraceStore,
    InMemoryModelUsageStore,
    ModelProfile,
    ModelRegistry,
    ModelRequest,
    ModelResponse,
    ModelRouter,
    MasWorkflowExecutor,
    PlatformControlPlane,
    PlatformServices,
    ProjectCreate,
    ProviderAdapterRegistry,
    ProviderProfile,
    ProviderRegistry,
    ProviderType,
    RoleModelPolicy,
    RoleModelPolicyRegistry,
    RoleRegistry,
    ToolPolicyRegistry,
    build_platform_router,
    default_role_definitions,
)


class WorkflowAdapter:
    def __init__(self):
        self.calls = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append((request.model.id, request.messages[-1]["content"]))
        outputs = {
            "pm_model": {
                "summary": "Provider-neutral requirements",
                "requirements": ["Support role collaboration"],
                "constraints": ["Keep providers decoupled"],
                "acceptanceCriteria": ["Artifacts validate"],
            },
            "architect_model": {
                "summary": "Provider-neutral architecture",
                "decisions": ["Use registries and adapters"],
                "constraints": ["No provider branching in agents"],
                "consequences": ["Routing remains traceable"],
            },
            "lead_model": {
                "summary": "Ordered implementation plan",
                "tasks": [
                    {
                        "id": "task-1",
                        "title": "Build registry",
                        "description": "Implement the registry boundary",
                        "dependsOn": [],
                    }
                ],
            },
            "reviewer_model": {
                "summary": "Plan requires one revision",
                "approved": False,
                "findings": [
                    {
                        "severity": "warning",
                        "message": "Add failure-path tests",
                        "sourceArtifactId": "",
                    }
                ],
            },
        }
        return ModelResponse(
            output=json.dumps(outputs[request.model.id]),
            inputTokens=20,
            outputTokens=10,
            latencyMs=2,
        )

    async def stream(self, request):
        yield await self.complete(request)

    async def health_check(self, provider, model):
        return HealthResult(status=HealthStatus.HEALTHY)


def workflow_components():
    providers = ProviderRegistry(
        [
            ProviderProfile(
                id="provider_a",
                name="Provider A",
                providerType="openai-compatible",
                baseUrl="https://a.invalid/v1",
                healthStatus="healthy",
            ),
            ProviderProfile(
                id="provider_b",
                name="Provider B",
                providerType="openai-compatible",
                baseUrl="https://b.invalid/v1",
                healthStatus="healthy",
            ),
        ]
    )
    models = ModelRegistry(
        [
            ModelProfile(
                id="pm_model",
                providerId="provider_a",
                modelName="pm-model",
                displayName="PM Model",
                capabilities={"text"},
                healthStatus="healthy",
            ),
            ModelProfile(
                id="architect_model",
                providerId="provider_b",
                modelName="architect-model",
                displayName="Architect Model",
                capabilities={"text"},
                healthStatus="healthy",
            ),
            ModelProfile(
                id="lead_model",
                providerId="provider_a",
                modelName="lead-model",
                displayName="Lead Model",
                capabilities={"text"},
                healthStatus="healthy",
            ),
            ModelProfile(
                id="reviewer_model",
                providerId="provider_b",
                modelName="review-model",
                displayName="Reviewer Model",
                capabilities={"text"},
                healthStatus="healthy",
            ),
        ]
    )
    policies = RoleModelPolicyRegistry(
        [
            RoleModelPolicy(
                id="pm_policy",
                roleId="product_manager",
                primaryModelIds=["pm_model"],
            ),
            RoleModelPolicy(
                id="architect_policy",
                roleId="solution_architect",
                primaryModelIds=["architect_model"],
            ),
            RoleModelPolicy(
                id="lead_policy",
                roleId="technical_lead",
                primaryModelIds=["lead_model"],
            ),
            RoleModelPolicy(
                id="reviewer_policy",
                roleId="reviewer",
                primaryModelIds=["lead_model"],
                fallbackModelIds=["reviewer_model"],
                excludeSameProviderAsProducer=True,
            ),
        ]
    )
    profiles = {
        role_id: AgentProfile(
            id=f"{role_id}_profile",
            name=f"{role_id} agent",
            agentName=f"{role_id}_agent",
            roleId=role_id,
            modelPolicyId=policy_id,
            toolPolicyId="no_tools",
            promptKey=f"{role_id}_prompt",
        )
        for role_id, policy_id in (
            ("product_manager", "pm_policy"),
            ("solution_architect", "architect_policy"),
            ("technical_lead", "lead_policy"),
            ("reviewer", "reviewer_policy"),
        )
    }
    profile_registry = AgentProfileRegistry(profiles.values())
    usage = InMemoryModelUsageStore()
    traces = InMemoryExecutionTraceStore()
    artifacts = InMemoryArtifactStore()
    adapter = WorkflowAdapter()
    adapters = ProviderAdapterRegistry()
    adapters.register(ProviderType.OPENAI_COMPATIBLE, adapter)
    router = ModelRouter(
        name="model_router",
        provider_registry=providers,
        model_registry=models,
        role_registry=RoleRegistry(default_role_definitions()),
        policy_registry=policies,
        agent_profile_registry=profile_registry,
        adapter_registry=adapters,
        usage_store=usage,
        trace_store=traces,
    )
    agents = [
        ChatAgent(
            name=profile.agent_name,
            llm_model="model_router",
            prompt=f"You are the {profile.name}.",
        )
        for profile in profiles.values()
    ]
    workflow = BasicRoleWorkflow(
        name="project_workflow",
        agent_profiles=profiles,
        artifact_store=artifacts,
        trace_store=traces,
    )
    return router, agents, workflow, adapter, usage, traces, artifacts


@pytest.mark.asyncio
async def test_four_role_workflow_creates_artifact_chain():
    router, agents, workflow, adapter, usage, traces, artifacts = workflow_components()
    async with MAS(
        name="platform_workflow_test", oxy_space=[router, *agents, workflow]
    ) as mas:
        response = await mas.chat_with_agent(
            {
                "query": "Build a provider-neutral collaboration platform",
                "project_id": "project-1",
                "is_async_storage": False,
            }
        )

    assert response.output["projectId"] == "project-1"
    result_artifacts = response.output["artifacts"]
    assert [item["type"] for item in result_artifacts] == [
        "RequirementSpec",
        "ArchitectureDecision",
        "TaskGraph",
        "ReviewReport",
    ]
    assert len(artifacts.list("project-1")) == 4
    assert result_artifacts[0]["content"]["requirements"] == [
        "Support role collaboration"
    ]
    assert result_artifacts[2]["content"]["tasks"][0]["title"] == "Build registry"
    assert result_artifacts[3]["content"]["findings"][0]["severity"] == "warning"
    assert result_artifacts[1]["sourceArtifactIds"] == [result_artifacts[0]["id"]]
    assert result_artifacts[2]["sourceArtifactIds"] == [result_artifacts[1]["id"]]
    assert result_artifacts[3]["sourceArtifactIds"] == [result_artifacts[2]["id"]]
    assert [call[0] for call in adapter.calls] == [
        "pm_model",
        "architect_model",
        "lead_model",
        "reviewer_model",
    ]
    assert len(usage.list()) == 4
    assert len(traces.route_decisions()) == 4
    assert len(traces.workflow_events(run_id=response.output["runId"])) == 12


@pytest.mark.asyncio
async def test_legacy_mas_and_chat_agent_still_run():
    async def legacy_response(_request):
        return "legacy-ok"

    llm = MockLLM(name="legacy_llm", func_mock_process=legacy_response)
    agent = ChatAgent(
        name="legacy_agent",
        llm_model="legacy_llm",
        is_master=True,
    )
    async with MAS(name="legacy_regression", oxy_space=[llm, agent]) as mas:
        response = await mas.chat_with_agent(
            {"query": "hello", "is_async_storage": False}
        )
    assert response.output == "legacy-ok"


@pytest.mark.asyncio
async def test_web_api_launches_real_role_workflow_and_exposes_results():
    router, agents, workflow, _adapter, usage, traces, artifacts = (
        workflow_components()
    )
    services = PlatformServices(
        artifacts=artifacts,
        control_plane=PlatformControlPlane(
            providers=router.provider_registry,
            models=router.model_registry,
            roles=router.role_registry,
            agents=router.agent_profile_registry,
            model_policies=router.policy_registry,
            tool_policies=ToolPolicyRegistry(),
            usage=usage,
            traces=traces,
            adapters=router.adapter_registry,
        ),
    )
    project = await services.create_project(ProjectCreate(name="Web workflow"))
    app = FastAPI()
    app.include_router(build_platform_router(services))

    async with MAS(
        name="platform_web_workflow_test",
        oxy_space=[router, *agents, workflow],
    ) as mas:
        services.workflow_executor = MasWorkflowExecutor(mas, workflow.name)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            launch = await client.post(
                f"/api/v1/platform/projects/{project.id}/workflows/runs",
                json={
                    "name": "Web four-role run",
                    "idea": "Build a traceable project collaboration platform",
                },
            )
            assert launch.status_code == 202
            run_id = launch.json()["data"]["run"]["runId"]
            await services.wait_for_workflow(run_id)

            run_response = await client.get(
                f"/api/v1/platform/workflows/runs/{run_id}"
            )
            artifact_response = await client.get(
                f"/api/v1/platform/projects/{project.id}/artifacts"
            )
            stream_response = await client.get(
                f"/api/v1/platform/workflows/runs/{run_id}/stream"
            )

    run = run_response.json()["data"]["run"]
    assert run["name"] == "Web four-role run"
    assert run["status"] == "awaiting-implementation"
    assert run["currentPhase"] == "implementation"
    assert [item["type"] for item in artifact_response.json()["data"]["items"]] == [
        "ReviewReport",
        "TaskGraph",
        "ArchitectureDecision",
        "RequirementSpec",
    ]
    assert len(usage.list()) == 4
    assert stream_response.status_code == 200
    assert "artifact.created" in stream_response.text
    assert "Provider-neutral requirements" not in stream_response.text
    assert "workflow.awaitingImplementation" in stream_response.text
