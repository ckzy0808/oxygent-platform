"""Run the phase-one, four-role Artifact workflow with environment configuration."""

import asyncio
import json
import os

from oxygent import Config, MAS
from oxygent.oxy import ChatAgent
from oxygent.platform import (
    AgentProfile,
    AgentProfileRegistry,
    BasicRoleWorkflow,
    EnvironmentCredentialResolver,
    InMemoryArtifactStore,
    InMemoryExecutionTraceStore,
    InMemoryModelUsageStore,
    ModelProfile,
    ModelRegistry,
    ModelRouter,
    ProviderProfile,
    ProviderRegistry,
    ProviderType,
    RoleModelPolicy,
    RoleModelPolicyRegistry,
    RoleRegistry,
    ToolPolicy,
    ToolPolicyRegistry,
    default_provider_adapters,
    default_role_definitions,
)


ROLE_CONFIG = {
    "product_manager": "PM",
    "solution_architect": "ARCHITECT",
    "technical_lead": "LEAD",
    "reviewer": "REVIEWER",
}

ROLE_PROMPTS = {
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


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"missing {name}; copy examples/platform/.env.multi_role.example to .env"
        )
    return value


def build_platform():
    providers = []
    models = []
    policies = []
    profiles = []
    agents = []

    for role_id, prefix in ROLE_CONFIG.items():
        provider_id = f"{role_id}_provider"
        model_id = f"{role_id}_model"
        policy_id = f"{role_id}_policy"
        agent_id = f"{role_id}_agent_profile"
        agent_name = f"{role_id}_agent"
        providers.append(
            ProviderProfile(
                id=provider_id,
                name=f"{role_id} provider",
                providerType=ProviderType(
                    required_env(f"OXYGENT_{prefix}_PROVIDER_TYPE")
                ),
                baseUrl=required_env(f"OXYGENT_{prefix}_BASE_URL"),
                credentialReference=f"env:OXYGENT_{prefix}_API_KEY",
                timeout=float(os.getenv(f"OXYGENT_{prefix}_TIMEOUT", "120")),
                healthStatus="healthy",
            )
        )
        models.append(
            ModelProfile(
                id=model_id,
                providerId=provider_id,
                modelName=required_env(f"OXYGENT_{prefix}_MODEL"),
                displayName=f"{role_id} model",
                capabilities={"text", "structured-output"},
                healthStatus="healthy",
            )
        )
        fallback_ids = [
            f"{other_role}_model" for other_role in ROLE_CONFIG if other_role != role_id
        ]
        policies.append(
            RoleModelPolicy(
                id=policy_id,
                roleId=role_id,
                routingMode="priority",
                primaryModelIds=[model_id],
                fallbackModelIds=fallback_ids,
                requiredCapabilities={"text"},
                excludeSameProviderAsProducer=role_id == "reviewer",
            )
        )
        profiles.append(
            AgentProfile(
                id=agent_id,
                name=ROLE_PROMPTS[role_id].split(".", 1)[0],
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
                desc=f"Phase-one {role_id} role agent",
                llm_model="platform_model_router",
                prompt=ROLE_PROMPTS[role_id],
            )
        )

    provider_registry = ProviderRegistry(providers)
    model_registry = ModelRegistry(models)
    role_registry = RoleRegistry(default_role_definitions())
    policy_registry = RoleModelPolicyRegistry(policies)
    profile_registry = AgentProfileRegistry(profiles)
    tool_policy_registry = ToolPolicyRegistry(
        [ToolPolicy(id="no_tools", name="No tools in phase-one workflow")]
    )
    for profile in profiles:
        tool_policy_registry.get(profile.tool_policy_id)
    usage_store = InMemoryModelUsageStore()
    trace_store = InMemoryExecutionTraceStore()
    artifact_store = InMemoryArtifactStore()
    router = ModelRouter(
        name="platform_model_router",
        provider_registry=provider_registry,
        model_registry=model_registry,
        role_registry=role_registry,
        policy_registry=policy_registry,
        agent_profile_registry=profile_registry,
        adapter_registry=default_provider_adapters(EnvironmentCredentialResolver()),
        usage_store=usage_store,
        trace_store=trace_store,
    )
    workflow = BasicRoleWorkflow(
        name="multi_role_workflow",
        agent_profiles={profile.role_id: profile for profile in profiles},
        artifact_store=artifact_store,
        trace_store=trace_store,
    )
    return [router, *agents, workflow], artifact_store, usage_store, trace_store


async def main() -> None:
    Config.set_server_auto_open_webpage(False)
    oxy_space, artifact_store, usage_store, trace_store = build_platform()
    idea = os.getenv(
        "OXYGENT_PLATFORM_IDEA",
        "Build a project-centered multi-role Agent collaboration platform.",
    )
    async with MAS(oxy_space=oxy_space) as mas:
        response = await mas.chat_with_agent(
            payload={"query": idea, "project_id": "multi-role-demo"}
        )
        print(json.dumps(response.output, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "artifactCount": len(artifact_store.list()),
                    "modelUsage": [
                        usage.model_dump(mode="json", by_alias=True)
                        for usage in usage_store.list()
                    ],
                    "routeDecisions": [
                        trace.model_dump(mode="json", by_alias=True)
                        for trace in trace_store.route_decisions()
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
