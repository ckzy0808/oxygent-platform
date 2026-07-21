"""Run the Project/Artifact UI with local, credential-free demo data."""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oxygent import MAS, oxy
from oxygent.platform import (
    AgentProfile,
    AgentProfileRegistry,
    ChangeContract,
    CodeTaskCreate,
    EngineeringStatus,
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
    RepositoryRegistration,
    RoleModelPolicy,
    RoleModelPolicyRegistry,
    RoleRegistry,
    RouteDecisionTrace,
    ToolPolicy,
    ToolPolicyRegistry,
    WorkflowEvent,
    WorkflowPhase,
    build_platform_router,
    default_role_definitions,
)


async def demo_response(_request):
    return "The existing OxyGent Chat remains available beside Project Workspace."


class DemoHealthAdapter:
    """Credential-free health adapter used only by this local UI demo."""

    async def health_check(self, _provider, _model):
        return HealthResult(status=HealthStatus.HEALTHY, latencyMs=18.0)


def seed_workflow_timeline(
    traces: InMemoryExecutionTraceStore, project_id: str
) -> None:
    """Seed a product-safe timeline; no model prompts or raw output are stored."""
    started_at = datetime.now(timezone.utc) - timedelta(minutes=28)
    run_id = "workflow-run-001"
    task_id = "workflow-timeline-task"
    phases = [
        (
            WorkflowPhase.REQUIREMENT,
            "product_manager",
            "product_manager-profile",
            "provider-a",
            "pm-model",
            EngineeringStatus.ANALYZING,
            "Requirements and acceptance criteria are ready.",
            ["artifact-read", "artifact-write"],
            {"id": "requirement-spec-001", "type": "RequirementSpec"},
            0.008,
            1240,
        ),
        (
            WorkflowPhase.ARCHITECTURE,
            "solution_architect",
            "solution_architect-profile",
            "provider-b",
            "architect-model",
            EngineeringStatus.PLANNING,
            "Architecture boundaries and decisions are documented.",
            ["artifact-read", "dependency-map"],
            {"id": "architecture-decision-001", "type": "ArchitectureDecision"},
            0.012,
            1680,
        ),
        (
            WorkflowPhase.PLAN,
            "technical_lead",
            "technical_lead-profile",
            "provider-c",
            "lead-model",
            EngineeringStatus.PLANNING,
            "The implementation task graph and dependencies are ready.",
            ["artifact-read", "task-graph"],
            {"id": "task-graph-001", "type": "TaskGraph"},
            0.016,
            1420,
        ),
        (
            WorkflowPhase.IMPLEMENTATION,
            "technical_lead",
            "technical_lead-profile",
            "provider-c",
            "lead-model",
            EngineeringStatus.IMPLEMENTING,
            "Implementation scope was evaluated; code writing remains gated.",
            ["repository-read", "file-search"],
            None,
            0.014,
            2040,
        ),
        (
            WorkflowPhase.VERIFICATION,
            "reviewer",
            "reviewer-profile",
            "provider-d",
            "reviewer-model",
            EngineeringStatus.TESTING,
            "Configured checks completed with recorded exit codes.",
            ["verification-profile"],
            None,
            0.010,
            1860,
        ),
        (
            WorkflowPhase.REVIEW,
            "reviewer",
            "reviewer-profile",
            "provider-d",
            "reviewer-model",
            EngineeringStatus.REVIEWING,
            "Independent review completed without exposing private reasoning.",
            ["artifact-read"],
            {"id": "review-report-001", "type": "ReviewReport"},
            0.020,
            1540,
        ),
    ]
    for index, (
        phase,
        role,
        agent_id,
        provider_id,
        model_id,
        active_status,
        summary,
        tools,
        artifact,
        cost,
        duration_ms,
    ) in enumerate(phases):
        phase_started = started_at + timedelta(minutes=index * 4)
        start_payload = {
            "status": active_status.value,
            "summary": f"{phase.value.title()} phase started.",
        }
        if index == 0:
            start_payload["runName"] = "Platform workspace delivery"
        traces.append_workflow_event(
            WorkflowEvent(
                eventId=f"workflow-{index}-started",
                projectId=project_id,
                taskId=task_id,
                runId=run_id,
                agentId=agent_id,
                role=role,
                providerId=provider_id,
                modelId=model_id,
                phase=phase,
                eventType="phase.started",
                timestamp=phase_started,
                payload=start_payload,
            )
        )
        completed_payload = {
            "status": EngineeringStatus.COMPLETED.value,
            "summary": summary,
            "toolsUsed": tools,
            "cost": cost,
            "durationMs": duration_ms,
        }
        if artifact:
            completed_payload["artifact"] = {
                **artifact,
                "schemaVersion": "1.0",
                "validationStatus": "valid",
            }
        traces.append_workflow_event(
            WorkflowEvent(
                eventId=f"workflow-{index}-completed",
                projectId=project_id,
                taskId=task_id,
                runId=run_id,
                agentId=agent_id,
                role=role,
                providerId=provider_id,
                modelId=model_id,
                phase=phase,
                eventType="phase.completed",
                timestamp=phase_started + timedelta(minutes=3),
                payload=completed_payload,
            )
        )
    traces.append_workflow_event(
        WorkflowEvent(
            eventId="workflow-approval-requested",
            projectId=project_id,
            taskId=task_id,
            runId=run_id,
            agentId="human-approval",
            role="approver",
            phase=WorkflowPhase.APPROVAL,
            eventType="approval.requested",
            timestamp=started_at + timedelta(minutes=27),
            payload={
                "status": EngineeringStatus.AWAITING_APPROVAL.value,
                "summary": "Workflow is ready for explicit human approval.",
            },
        )
    )


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
    seed_workflow_timeline(traces, project_id)
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
    demo_repository = os.getenv("OXYGENT_DEMO_REPOSITORY")
    services = (
        PlatformServices.with_code_workspace(
            repository_roots={"demo-repository": Path(demo_repository)},
            workspace_root=Path(
                os.getenv("OXYGENT_CODE_WORKSPACE_ROOT", "/tmp/oxygent-code-worktrees")
            ),
        )
        if demo_repository
        else PlatformServices()
    )
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
    project_task = await services.create_task_from_chat(
        project.id,
        ProjectTaskFromChat(
            title="Review the Project workspace foundation",
            objective="Verify Project isolation, Artifact provenance, and Chat handoff.",
            sourceTraceId="demo-trace-reference",
            attachmentReferences=["workspace_notes.md"],
            sourceArtifactIds=[requirement.id],
        ),
    )
    if demo_repository and os.getenv("OXYGENT_DEMO_SEED_CODE_TASK") == "1":
        metadata = await services.worktrees.inspect_repository("demo-repository")
        default_branch = metadata["defaultBranch"]
        repository = await services.register_repository(
            project.id,
            RepositoryRegistration(
                name=Path(demo_repository).name,
                rootReference="demo-repository",
                defaultBranch=default_branch,
                allowedBaseBranches=[default_branch],
            ),
        )
        await services.create_code_task(
            project.id,
            CodeTaskCreate(
                repositoryId=repository.id,
                projectTaskId=project_task.id,
                baseBranch=default_branch,
                changeContract=ChangeContract(
                    objective="Add repository isolation and bounded code inspection.",
                    acceptanceCriteria=[
                        "The source working directory remains unchanged",
                        "Repository reads stay inside the task worktree",
                        "Scope limits are enforced by server code",
                    ],
                    allowedPaths=["oxygent/**", "tests/**", "docs/**", "examples/**"],
                    forbiddenPaths=[".env*", "**/*.key", "**/*.pem"],
                    maxChangedFiles=20,
                    maxDiffLines=1000,
                ),
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
