"""Run the Project/Artifact UI with local, credential-free demo data."""

import asyncio
import os

from oxygent import MAS, oxy
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
    ProjectCreate,
    ProjectTaskFromChat,
    ProviderAdapterRegistry,
    ProviderProfile,
    ProviderRegistry,
    ProviderType,
    RequirementSpec,
    RequirementSpecContent,
    RoleModelPolicy,
    RoleModelPolicyRegistry,
    RoleRegistry,
    RouteDecisionTrace,
    ToolPolicy,
    ToolPolicyRegistry,
    build_platform_router,
    default_role_definitions,
)


async def demo_response(_request):
    return "The existing OxyGent Chat remains available beside Project Workspace."


class DemoHealthAdapter:
    """Credential-free health adapter used only by this local UI demo."""

    async def health_check(self, _provider, _model):
        return HealthResult(status=HealthStatus.HEALTHY, latencyMs=18.0)


def build_control_plane(project_id: str) -> PlatformControlPlane:
    provider_specs = [
        (
            "provider-a",
            "OpenAI-Compatible A",
            ProviderType.OPENAI_COMPATIBLE,
            "https://provider-a.invalid/v1",
        ),
        (
            "provider-b",
            "Gemini B",
            ProviderType.GEMINI,
            "https://provider-b.invalid/v1beta",
        ),
        (
            "provider-c",
            "Ollama C",
            ProviderType.OLLAMA,
            "http://127.0.0.1:11434",
        ),
        (
            "provider-d",
            "Independent Review D",
            ProviderType.OPENAI_COMPATIBLE,
            "https://provider-d.invalid/v1",
        ),
    ]
    providers = ProviderRegistry(
        ProviderProfile(
            id=provider_id,
            name=name,
            providerType=provider_type,
            baseUrl=base_url,
            credentialReference=f"env:DEMO_{provider_id.upper().replace('-', '_')}_KEY",
            healthStatus=HealthStatus.HEALTHY,
        )
        for provider_id, name, provider_type, base_url in provider_specs
    )
    model_specs = [
        (
            "pm-model",
            "provider-a",
            "planner-v1",
            "Atlas Planner",
            {"text", "structured-output"},
        ),
        (
            "architect-model",
            "provider-b",
            "design-v1",
            "Gemini Design",
            {"text", "structured-output", "long-context"},
        ),
        (
            "lead-model",
            "provider-c",
            "code-v1",
            "Local Code",
            {"text", "structured-output", "code"},
        ),
        (
            "reviewer-model",
            "provider-d",
            "review-v1",
            "Independent Review",
            {"text", "structured-output", "review"},
        ),
    ]
    models = ModelRegistry(
        ModelProfile(
            id=model_id,
            providerId=provider_id,
            modelName=model_name,
            displayName=display_name,
            capabilities=capabilities,
            contextWindow=128000,
            costTier=index + 1,
            latencyTier=4 - index,
            healthStatus=HealthStatus.HEALTHY,
            inputCostPerMillion=0.5 + index,
            outputCostPerMillion=1.5 + index,
        )
        for index, (
            model_id,
            provider_id,
            model_name,
            display_name,
            capabilities,
        ) in enumerate(model_specs)
    )
    role_ids = [
        "product_manager",
        "solution_architect",
        "technical_lead",
        "reviewer",
    ]
    policies = RoleModelPolicyRegistry(
        RoleModelPolicy(
            id=f"{role_id}-policy",
            roleId=role_id,
            primaryModelIds=[model_specs[index][0]],
            fallbackModelIds=[model_specs[(index + 1) % len(model_specs)][0]],
            requiredCapabilities={"text", "structured-output"},
            maxCostPerRun=0.25,
            excludeSameProviderAsProducer=role_id == "reviewer",
        )
        for index, role_id in enumerate(role_ids)
    )
    tools = ToolPolicyRegistry(
        [
            ToolPolicy(
                id="planning-tools",
                name="Planning tools",
                allowedTools=["artifact-read", "artifact-write"],
            ),
            ToolPolicy(
                id="architecture-tools",
                name="Architecture tools",
                allowedTools=["artifact-read", "dependency-map"],
            ),
            ToolPolicy(
                id="lead-tools",
                name="Technical planning tools",
                allowedTools=["artifact-read", "task-graph"],
            ),
            ToolPolicy(
                id="review-tools",
                name="Independent review tools",
                allowedTools=["artifact-read"],
                deniedTools=["artifact-write"],
            ),
        ]
    )
    tool_ids = [
        "planning-tools",
        "architecture-tools",
        "lead-tools",
        "review-tools",
    ]
    agents = AgentProfileRegistry(
        AgentProfile(
            id=f"{role_id}-profile",
            name=default_role_definitions()[index].name,
            agentName=f"{role_id}_agent",
            roleId=role_id,
            modelPolicyId=f"{role_id}-policy",
            toolPolicyId=tool_ids[index],
            promptKey=f"platform.{role_id}.v1",
        )
        for index, role_id in enumerate(role_ids)
    )
    usage = InMemoryModelUsageStore()
    traces = InMemoryExecutionTraceStore()
    for index, role_id in enumerate(role_ids):
        model_id, provider_id = model_specs[index][0], model_specs[index][1]
        agent_id = f"{role_id}-profile"
        usage.append(
            ModelUsage(
                projectId=project_id,
                taskId="demo-workflow-task",
                runId="demo-run",
                roleId=role_id,
                agentId=agent_id,
                providerId=provider_id,
                modelId=model_id,
                inputTokens=900 + index * 125,
                outputTokens=420 + index * 80,
                latencyMs=620 + index * 140,
                estimatedCost=0.008 + index * 0.004,
                status=InvocationStatus.SUCCEEDED,
            )
        )
        traces.append_route_decision(
            RouteDecisionTrace(
                id=f"route-{role_id}",
                projectId=project_id,
                taskId="demo-workflow-task",
                runId="demo-run",
                roleId=role_id,
                agentId=agent_id,
                taskType="structured-collaboration",
                selectedProviderId=provider_id,
                selectedModelId=model_id,
                selectionReason=(
                    "Selected primary model by priority; required capabilities "
                    "matched; Provider health is healthy."
                ),
                fallbackChain=[model_specs[(index + 1) % len(model_specs)][0]],
                requiredCapabilities=["structured-output", "text"],
            )
        )
    adapters = ProviderAdapterRegistry()
    demo_adapter = DemoHealthAdapter()
    for provider_type in {
        ProviderType.OPENAI_COMPATIBLE,
        ProviderType.GEMINI,
        ProviderType.OLLAMA,
    }:
        adapters.register(provider_type, demo_adapter)
    return PlatformControlPlane(
        providers=providers,
        models=models,
        roles=RoleRegistry(default_role_definitions()),
        agents=agents,
        model_policies=policies,
        tool_policies=tools,
        usage=usage,
        traces=traces,
        adapters=adapters,
        allow_provider_mutations=True,
    )


async def build_services() -> PlatformServices:
    services = PlatformServices()
    project = await services.create_project(
        ProjectCreate(
            name="Agent Platform Workspace",
            description="A generic workspace for structured multi-role collaboration.",
            repository="Not linked",
            team=[
                "Product Manager",
                "Solution Architect",
                "Technical Lead",
                "Reviewer",
            ],
        )
    )
    requirement = services.artifacts.append(
        RequirementSpec(
            projectId=project.id,
            taskId="demo-workflow-task",
            producerRole="product_manager",
            producerAgent="pm_agent",
            providerId="provider-a",
            modelId="model-a",
            content=RequirementSpecContent(
                summary="Keep role outputs structured and traceable",
                requirements=[
                    "Preserve the existing Chat experience",
                    "Pass structured Artifacts between roles",
                ],
                acceptanceCriteria=[
                    "Every task references its Project",
                    "Artifact revisions never overwrite earlier versions",
                ],
            ),
        )
    )
    await services.create_task_from_chat(
        project.id,
        ProjectTaskFromChat(
            title="Review the Project workspace foundation",
            objective="Verify Project isolation, Artifact provenance, and Chat handoff.",
            sourceTraceId="demo-trace-reference",
            attachmentReferences=["workspace_notes.md"],
            sourceArtifactIds=[requirement.id],
        ),
    )
    services.control_plane = build_control_plane(project.id)
    return services


async def main() -> None:
    services = await build_services()
    llm = oxy.MockLLM(name="projects_demo_llm", func_mock_process=demo_response)
    agent = oxy.ChatAgent(
        name="projects_demo_agent",
        llm_model="projects_demo_llm",
        is_master=True,
    )
    port = int(os.getenv("OXYGENT_PROJECT_DEMO_PORT", "18080"))
    async with MAS(name="projects_web_demo", oxy_space=[llm, agent]) as mas:
        await mas.start_web_service(
            host="127.0.0.1",
            port=port,
            routers=[build_platform_router(services)],
            welcome_message="Project Workspace demo is ready.",
        )


if __name__ == "__main__":
    asyncio.run(main())
