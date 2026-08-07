"""Runtime bridge between the platform API and an OxyGent role workflow."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from pydantic import Field

from oxygent.oxy import ChatAgent
from oxygent.schemas import OxyState

from .artifacts import InMemoryArtifactStore
from .common import PlatformModel
from .control_plane import PlatformControlPlane
from .credentials import CredentialResolver, default_credential_resolver
from .profiles import (
    AgentProfile,
    HealthStatus,
    ModelProfile,
    ProviderProfile,
    ProviderType,
    RoleModelPolicy,
    ToolPolicy,
    default_role_definitions,
)
from .provider_adapters import default_provider_adapters
from .registries import (
    AgentProfileRegistry,
    ModelRegistry,
    ProviderRegistry,
    RoleModelPolicyRegistry,
    RoleRegistry,
    ToolPolicyRegistry,
)
from .routing import ModelRouter
from .tracing import InMemoryExecutionTraceStore
from .usage import InMemoryModelUsageStore
from .workflow import BasicRoleWorkflow

if TYPE_CHECKING:
    from oxygent import MAS


ROLE_ENV_PREFIXES: dict[str, str] = {
    "product_manager": "PM",
    "solution_architect": "ARCHITECT",
    "technical_lead": "LEAD",
    "reviewer": "REVIEWER",
}

ROLE_PROMPTS: dict[str, str] = {
    "product_manager": (
        "You are a Product Manager. Convert the idea into explicit requirements, "
        "constraints, and acceptance criteria. Do not design implementation details."
    ),
    "solution_architect": (
        "You are a Solution Architect. Work only from the supplied RequirementSpec "
        "and state architecture decisions, constraints, and consequences."
    ),
    "technical_lead": (
        "You are a Technical Lead. Work only from the supplied ArchitectureDecision "
        "and produce ordered, dependency-aware implementation tasks."
    ),
    "reviewer": (
        "You are an independent Reviewer. Identify omissions, risks, and blocking "
        "issues in the supplied TaskGraph."
    ),
}


class WorkflowLaunchRequest(PlatformModel):
    """User-controlled fields accepted when a Project launches a workflow."""

    idea: str = Field(min_length=1, max_length=20_000)
    name: str = Field(default="", max_length=300)
    source_workspace_id: str | None = Field(default=None, max_length=160)
    source_analysis_id: str | None = Field(default=None, max_length=160)


class WorkflowExecutionRequest(PlatformModel):
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    idea: str = Field(min_length=1, max_length=20_000)
    name: str = Field(default="", max_length=300)


class WorkflowExecutor(Protocol):
    async def execute(self, request: WorkflowExecutionRequest) -> dict[str, Any]: ...


class MasWorkflowExecutor:
    """Invoke a named BasicRoleWorkflow through an already-running MAS."""

    def __init__(self, mas: MAS, workflow_name: str) -> None:
        self._mas = mas
        self._workflow_name = workflow_name

    async def execute(self, request: WorkflowExecutionRequest) -> dict[str, Any]:
        response = await self._mas.chat_with_agent(
            {
                "callee": self._workflow_name,
                "query": request.idea,
                "project_id": request.project_id,
                "task_id": request.task_id,
                "current_trace_id": request.run_id,
                "is_async_storage": False,
            }
        )
        if response.state is not OxyState.COMPLETED:
            raise RuntimeError("role workflow returned a failed state")
        return response.output if isinstance(response.output, dict) else {}


@dataclass
class EnvironmentWorkflowBundle:
    """Oxy objects and stores built from non-secret environment configuration."""

    oxy_space: list[Any]
    control_plane: PlatformControlPlane
    artifacts: InMemoryArtifactStore
    workflow_name: str


def _role_value(
    environment: Mapping[str, str], prefix: str, suffix: str
) -> tuple[str, str]:
    role_name = f"OXYGENT_{prefix}_{suffix}"
    shared_name = f"OXYGENT_SHARED_{suffix}"
    if environment.get(role_name, "").strip():
        return environment[role_name].strip(), role_name
    if environment.get(shared_name, "").strip():
        return environment[shared_name].strip(), shared_name
    raise RuntimeError(
        f"missing required workflow environment variable: {role_name} or {shared_name}"
    )


def environment_workflow_enabled(environment: Mapping[str, str] | None = None) -> bool:
    values = environment or os.environ
    return values.get("OXYGENT_ENABLE_REAL_WORKFLOW", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def build_environment_workflow_bundle(
    *,
    environment: Mapping[str, str] | None = None,
    credential_resolver: CredentialResolver | None = None,
    artifact_store: InMemoryArtifactStore | None = None,
    workflow_is_master: bool = False,
) -> EnvironmentWorkflowBundle:
    """Build the real four-role runtime without serializing resolved credentials."""

    values = environment or os.environ
    providers: dict[str, ProviderProfile] = {}
    models: list[ModelProfile] = []
    policies: list[RoleModelPolicy] = []
    profiles: list[AgentProfile] = []
    agents: list[ChatAgent] = []

    for role_id, prefix in ROLE_ENV_PREFIXES.items():
        provider_id = (
            values.get(f"OXYGENT_{prefix}_PROVIDER_ID", "").strip()
            or values.get("OXYGENT_SHARED_PROVIDER_ID", "").strip()
            or f"{role_id}_provider"
        )
        model_id = f"{role_id}_model"
        policy_id = f"{role_id}_policy"
        agent_id = f"{role_id}_agent_profile"
        agent_name = f"{role_id}_agent"
        provider_type_value, _ = _role_value(values, prefix, "PROVIDER_TYPE")
        provider_type = ProviderType(provider_type_value)
        base_url, _ = _role_value(values, prefix, "BASE_URL")
        model_name, _ = _role_value(values, prefix, "MODEL")
        credential_reference = values.get(
            f"OXYGENT_{prefix}_CREDENTIAL_REFERENCE", ""
        ).strip() or values.get("OXYGENT_SHARED_CREDENTIAL_REFERENCE", "").strip()
        if not credential_reference:
            try:
                _credential, credential_name = _role_value(
                    values, prefix, "API_KEY"
                )
                credential_reference = f"env:{credential_name}"
            except RuntimeError:
                if provider_type is not ProviderType.OLLAMA:
                    raise
        timeout = values.get(f"OXYGENT_{prefix}_TIMEOUT", "").strip() or values.get(
            "OXYGENT_SHARED_TIMEOUT", "120"
        )
        provider = ProviderProfile(
            id=provider_id,
            name=(
                "Shared Workflow Provider"
                if values.get("OXYGENT_SHARED_PROVIDER_ID", "").strip()
                else f"{default_role_definitions()[len(models)].name} Provider"
            ),
            providerType=provider_type,
            baseUrl=base_url,
            credentialReference=credential_reference,
            timeout=float(timeout),
            healthStatus=HealthStatus.HEALTHY,
        )
        existing_provider = providers.get(provider_id)
        if existing_provider and (
            existing_provider.provider_type != provider.provider_type
            or existing_provider.base_url != provider.base_url
            or existing_provider.credential_reference != provider.credential_reference
            or existing_provider.timeout != provider.timeout
        ):
            raise RuntimeError(
                f"roles sharing provider ID {provider_id} must use identical settings"
            )
        providers.setdefault(provider_id, provider)
        models.append(
            ModelProfile(
                id=model_id,
                providerId=provider_id,
                modelName=model_name,
                displayName=f"{default_role_definitions()[len(models)].name} Model",
                capabilities={"text", "structured-output"},
                healthStatus=HealthStatus.HEALTHY,
            )
        )
        fallback_ids = [
            f"{other_role}_model"
            for other_role in ROLE_ENV_PREFIXES
            if other_role != role_id
        ]
        exclude_producer = role_id == "reviewer" and values.get(
            "OXYGENT_REVIEWER_EXCLUDE_PRODUCER_PROVIDER", "1"
        ).strip().lower() not in {"0", "false", "no", "off"}
        policies.append(
            RoleModelPolicy(
                id=policy_id,
                roleId=role_id,
                routingMode="priority",
                primaryModelIds=[model_id],
                fallbackModelIds=fallback_ids,
                requiredCapabilities={"text"},
                excludeSameProviderAsProducer=exclude_producer,
            )
        )
        profiles.append(
            AgentProfile(
                id=agent_id,
                name=default_role_definitions()[len(profiles)].name,
                agentName=agent_name,
                roleId=role_id,
                modelPolicyId=policy_id,
                toolPolicyId="no_tools",
                promptKey=f"platform.{role_id}.v1",
            )
        )
        agents.append(
            ChatAgent(
                name=agent_name,
                desc=f"Platform {role_id} role agent",
                llm_model="platform_model_router",
                prompt=ROLE_PROMPTS[role_id],
            )
        )

    provider_registry = ProviderRegistry(providers.values())
    model_registry = ModelRegistry(models)
    role_registry = RoleRegistry(default_role_definitions())
    policy_registry = RoleModelPolicyRegistry(policies)
    profile_registry = AgentProfileRegistry(profiles)
    tool_policy_registry = ToolPolicyRegistry(
        [ToolPolicy(id="no_tools", name="No tools in the role workflow")]
    )
    usage_store = InMemoryModelUsageStore()
    trace_store = InMemoryExecutionTraceStore()
    artifacts = artifact_store or InMemoryArtifactStore()
    router = ModelRouter(
        name="platform_model_router",
        provider_registry=provider_registry,
        model_registry=model_registry,
        role_registry=role_registry,
        policy_registry=policy_registry,
        agent_profile_registry=profile_registry,
        adapter_registry=default_provider_adapters(
            credential_resolver or default_credential_resolver()
        ),
        usage_store=usage_store,
        trace_store=trace_store,
    )
    workflow_name = "multi_role_workflow"
    workflow = BasicRoleWorkflow(
        name=workflow_name,
        is_master=workflow_is_master,
        agent_profiles={profile.role_id: profile for profile in profiles},
        artifact_store=artifacts,
        trace_store=trace_store,
    )
    control_plane = PlatformControlPlane(
        providers=provider_registry,
        models=model_registry,
        roles=role_registry,
        agents=profile_registry,
        model_policies=policy_registry,
        tool_policies=tool_policy_registry,
        usage=usage_store,
        traces=trace_store,
        adapters=router.adapter_registry,
        allow_provider_mutations=True,
    )
    return EnvironmentWorkflowBundle(
        oxy_space=[router, *agents, workflow],
        control_plane=control_plane,
        artifacts=artifacts,
        workflow_name=workflow_name,
    )
