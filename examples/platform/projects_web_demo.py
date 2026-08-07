"""Run the Project UI, optionally backed by the real four-role model workflow."""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oxygent import MAS, oxy
from oxygent.platform import (
    ApprovalActionRequest,
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
    MasWorkflowExecutor,
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
    VerificationCommand,
    VerificationProfileCreate,
    VerificationSlot,
    WorkflowEvent,
    WorkflowPhase,
    build_platform_router,
    build_environment_workflow_bundle,
    default_role_definitions,
    environment_workflow_enabled,
)


async def demo_response(_request):
    return "OxyGent 原有对话能力与项目工作区均可正常使用。"


def configure_default_model_fallback() -> None:
    """Reuse the repository's default LLM for all roles when no role config exists.

    Only credential references are copied.  The API key remains in its original
    environment variable and is resolved at call time.
    """
    if environment_workflow_enabled():
        return
    required = (
        "DEFAULT_LLM_API_KEY",
        "DEFAULT_LLM_BASE_URL",
        "DEFAULT_LLM_MODEL_NAME",
    )
    if not all(os.getenv(name, "").strip() for name in required):
        return
    os.environ.setdefault("OXYGENT_ENABLE_REAL_WORKFLOW", "1")
    os.environ.setdefault("OXYGENT_SHARED_PROVIDER_ID", "default_openai_provider")
    os.environ.setdefault(
        "OXYGENT_SHARED_PROVIDER_TYPE",
        os.getenv("DEFAULT_LLM_PROVIDER_TYPE", "openai-responses"),
    )
    os.environ.setdefault("OXYGENT_SHARED_BASE_URL", os.environ["DEFAULT_LLM_BASE_URL"])
    os.environ.setdefault("OXYGENT_SHARED_MODEL", os.environ["DEFAULT_LLM_MODEL_NAME"])
    os.environ.setdefault(
        "OXYGENT_SHARED_CREDENTIAL_REFERENCE", "env:DEFAULT_LLM_API_KEY"
    )
    os.environ.setdefault("OXYGENT_REVIEWER_EXCLUDE_PRODUCER_PROVIDER", "0")


def configured_repository_roots() -> dict[str, Path]:
    """Return explicitly configured local Git repositories for Code Workspace.

    The demo is useful out of the box with its own repository, while deployments
    can replace that single source with an allow-listed path-separated list.  The
    browser only receives the opaque references, never these filesystem paths.
    """
    if os.getenv("OXYGENT_DISABLE_CODE_WORKSPACE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return {}
    configured = os.getenv("OXYGENT_CODE_REPOSITORIES", "").strip()
    if configured:
        return {
            f"local-repository-{index}": Path(value).expanduser()
            for index, value in enumerate(configured.split(os.pathsep), start=1)
            if value.strip()
        }
    demo_repository = os.getenv("OXYGENT_DEMO_REPOSITORY", "").strip()
    if demo_repository:
        return {"demo-repository": Path(demo_repository).expanduser()}
    return {"current-oxygent": Path(__file__).resolve().parents[2]}


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
            "需求和验收标准已就绪。",
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
            "架构边界和决策已记录。",
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
            "实现任务图和依赖关系已就绪。",
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
            "已评估实现范围，代码写入仍受门禁控制。",
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
            "已完成配置的检查并记录真实退出码。",
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
            "独立审查已完成，未暴露模型的私有推理过程。",
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
            "summary": "阶段已开始。",
        }
        if index == 0:
            start_payload["runName"] = "平台工作区交付"
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
                "summary": "工作流已准备好等待人工明确审批。",
            },
        )
    )


def build_control_plane(project_id: str) -> PlatformControlPlane:
    provider_specs = [
        (
            "provider-a",
            "OpenAI 兼容服务 A",
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
            "独立审查服务 D",
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
            "规划模型 A",
            {"text", "structured-output"},
        ),
        (
            "architect-model",
            "provider-b",
            "design-v1",
            "架构设计模型 B",
            {"text", "structured-output", "long-context"},
        ),
        (
            "lead-model",
            "provider-c",
            "code-v1",
            "本地代码模型 C",
            {"text", "structured-output", "code"},
        ),
        (
            "reviewer-model",
            "provider-d",
            "review-v1",
            "独立审查模型 D",
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
                name="产品规划工具",
                allowedTools=["artifact-read", "artifact-write"],
            ),
            ToolPolicy(
                id="architecture-tools",
                name="架构设计工具",
                allowedTools=["artifact-read", "dependency-map"],
            ),
            ToolPolicy(
                id="lead-tools",
                name="技术规划工具",
                allowedTools=["artifact-read", "task-graph"],
            ),
            ToolPolicy(
                id="review-tools",
                name="独立审查工具",
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
                    "按优先级选择主模型；所需能力匹配；服务商健康状态正常。"
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
    repository_roots = configured_repository_roots()
    primary_repository_reference = next(iter(repository_roots), None)
    services = (
        PlatformServices.with_code_workspace(
            repository_roots=repository_roots,
            workspace_root=Path(
                os.getenv("OXYGENT_CODE_WORKSPACE_ROOT", "/tmp/oxygent-code-worktrees")
            ),
            verification_executables={sys.executable, "git"},
        )
        if repository_roots
        else PlatformServices()
    )
    project = await services.create_project(
        ProjectCreate(
            name="智能体平台工作区",
            description="用于结构化多角色协作的通用工作区。",
            repository="未关联",
            team=[
                "产品经理",
                "解决方案架构师",
                "技术负责人",
                "审查员",
            ],
            settings={"monthlyBudget": 0.05},
        )
    )
    seed_demo_data = not environment_workflow_enabled() or os.getenv(
        "OXYGENT_SEED_DEMO_DATA", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    project_task = None
    if seed_demo_data:
        requirement = services.artifacts.append(
            RequirementSpec(
                projectId=project.id,
                taskId="demo-workflow-task",
                producerRole="product_manager",
                producerAgent="pm_agent",
                providerId="provider-a",
                modelId="model-a",
                content=RequirementSpecContent(
                    summary="保持角色输出结构化且可追踪",
                    requirements=[
                        "保留现有对话体验",
                        "在角色之间传递结构化产物",
                    ],
                    acceptanceCriteria=[
                        "每个任务都引用所属项目",
                        "产物修订不会覆盖早期版本",
                    ],
                ),
            )
        )
        project_task = await services.create_task_from_chat(
            project.id,
            ProjectTaskFromChat(
                title="审查项目工作区基础能力",
                objective="验证项目隔离、产物来源和对话任务交接。",
                sourceTraceId="demo-trace-reference",
                attachmentReferences=["workspace_notes.md"],
                sourceArtifactIds=[requirement.id],
            ),
        )
    if (
        primary_repository_reference
        and project_task is not None
        and os.getenv("OXYGENT_DEMO_SEED_CODE_TASK") == "1"
    ):
        metadata = await services.worktrees.inspect_repository(
            primary_repository_reference
        )
        default_branch = metadata["defaultBranch"]
        repository = await services.register_repository(
            project.id,
            RepositoryRegistration(
                name=repository_roots[primary_repository_reference].name,
                rootReference=primary_repository_reference,
                defaultBranch=default_branch,
                allowedBaseBranches=[default_branch],
            ),
        )
        verification_profile = await services.register_verification_profile(
            project.id,
            VerificationProfileCreate(
                repositoryId=repository.id,
                name="安全的本地检查",
                commands=[
                    VerificationCommand(
                        id="demo-unit-check",
                        name="代码仓库冒烟测试",
                        slot=VerificationSlot.UNIT,
                        argv=[
                            sys.executable,
                            "-c",
                            (
                                "from pathlib import Path; "
                                "assert Path('README.md').is_file(); "
                                "print('Repository smoke test passed')"
                            ),
                        ],
                        timeoutSeconds=30,
                    )
                ],
            ),
        )
        code_task = await services.create_code_task(
            project.id,
            CodeTaskCreate(
                repositoryId=repository.id,
                projectTaskId=project_task.id,
                baseBranch=default_branch,
                changeContract=ChangeContract(
                    objective="增加代码仓库隔离和有边界的代码检查能力。",
                    acceptanceCriteria=[
                        "源工作目录保持不变",
                        "代码仓库读取操作限制在任务 Worktree 内",
                        "范围限制由服务器代码强制执行",
                    ],
                    allowedPaths=["oxygent/**", "tests/**", "docs/**", "examples/**"],
                    forbiddenPaths=[".env*", "**/*.key", "**/*.pem"],
                    maxChangedFiles=20,
                    maxDiffLines=1000,
                    verificationProfileId=verification_profile.id,
                ),
            ),
        )
        if os.getenv("OXYGENT_DEMO_SEED_DIFF") == "1":
            demo_change = Path(code_task.worktree_path) / "docs/refactor/demo-change.md"
            demo_change.parent.mkdir(parents=True, exist_ok=True)
            demo_change.write_text(
                "# Demo change\n\nThis file exists only in the disposable demo worktree.\n",
                encoding="utf-8",
            )
            await services.run_verification(
                project.id,
                code_task.id,
                verification_profile.id,
                "demo-unit-check",
            )
            if os.getenv("OXYGENT_DEMO_SEED_APPROVAL") == "1":
                await services.approve_code_changes(
                    project.id,
                    code_task.id,
                    ApprovalActionRequest(
                        actorId="demo-human-reviewer",
                        reason="已在无凭证的本地演示中批准。",
                    ),
                )
    services.control_plane = build_control_plane(project.id)
    return services


async def main() -> None:
    configure_default_model_fallback()
    services = await build_services()
    port = int(os.getenv("OXYGENT_PROJECT_DEMO_PORT", "18080"))
    services.aider_proxy_base_url = (
        f"http://127.0.0.1:{port}/api/v1/platform/aider-proxy/v1"
    )
    llm = oxy.MockLLM(name="projects_demo_llm", func_mock_process=demo_response)
    agent = oxy.ChatAgent(
        name="projects_demo_agent",
        llm_model="projects_demo_llm",
        is_master=True,
    )
    workflow_bundle = None
    oxy_space = [llm, agent]
    if environment_workflow_enabled():
        workflow_bundle = build_environment_workflow_bundle(
            artifact_store=services.artifacts,
            workflow_is_master=False,
        )
        services.control_plane = workflow_bundle.control_plane
        oxy_space.extend(workflow_bundle.oxy_space)
    async with MAS(
        name="projects_web_demo",
        oxy_space=oxy_space,
        func_record_model_usage=services.record_mas_model_usage,
    ) as mas:
        if workflow_bundle is not None:
            services.workflow_executor = MasWorkflowExecutor(
                mas, workflow_bundle.workflow_name
            )
        await mas.start_web_service(
            host="127.0.0.1",
            port=port,
            routers=[build_platform_router(services)],
            welcome_message="项目工作区演示已就绪。",
        )


if __name__ == "__main__":
    asyncio.run(main())
