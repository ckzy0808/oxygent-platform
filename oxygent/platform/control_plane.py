"""Registry-backed control plane for Agents, Providers, Models, and policies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import Field

from .common import PlatformModel, utc_now
from .profiles import (
    AgentProfile,
    ModelProfile,
    ProviderProfile,
    ProviderType,
    RoleModelPolicy,
)
from .provider_adapters import HealthResult, ProviderAdapterRegistry
from .registries import (
    AgentProfileRegistry,
    ModelRegistry,
    ProviderRegistry,
    RoleModelPolicyRegistry,
    RoleRegistry,
    ToolPolicyRegistry,
)
from .tracing import InMemoryExecutionTraceStore, RouteDecisionTrace
from .usage import InMemoryModelUsageStore, InvocationStatus, ModelUsage


def _validate_credential_reference(value: str) -> str:
    if not value:
        return value
    allowed = ("env:", "secret:", "vault:", "keychain:")
    if not value.startswith(allowed):
        raise ValueError(
            "credentialReference must be an env:, secret:, vault:, or keychain: reference"
        )
    return value


class ProviderCreate(PlatformModel):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_.-]+$")
    name: str = Field(min_length=1, max_length=160)
    provider_type: ProviderType
    base_url: str = Field(min_length=1, max_length=1000)
    credential_reference: str = Field(default="", max_length=300)
    enabled: bool = True
    timeout: float = Field(default=120.0, gt=0, le=3600)


class ProviderUpdate(PlatformModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    base_url: str | None = Field(default=None, min_length=1, max_length=1000)
    credential_reference: str | None = Field(default=None, max_length=300)
    enabled: bool | None = None
    timeout: float | None = Field(default=None, gt=0, le=3600)


class ProviderHealthCheckRequest(PlatformModel):
    model_id: str | None = Field(default=None, max_length=160)


@dataclass
class PlatformControlPlane:
    """Explicit registries and runtime stores exposed by the product API."""

    providers: ProviderRegistry = field(default_factory=ProviderRegistry)
    models: ModelRegistry = field(default_factory=ModelRegistry)
    roles: RoleRegistry = field(default_factory=RoleRegistry)
    agents: AgentProfileRegistry = field(default_factory=AgentProfileRegistry)
    model_policies: RoleModelPolicyRegistry = field(
        default_factory=RoleModelPolicyRegistry
    )
    tool_policies: ToolPolicyRegistry = field(default_factory=ToolPolicyRegistry)
    usage: InMemoryModelUsageStore = field(default_factory=InMemoryModelUsageStore)
    traces: InMemoryExecutionTraceStore = field(
        default_factory=InMemoryExecutionTraceStore
    )
    adapters: ProviderAdapterRegistry = field(default_factory=ProviderAdapterRegistry)
    allow_provider_mutations: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.providers.list() or self.models.list() or self.agents.list())

    def create_provider(self, payload: ProviderCreate) -> ProviderProfile:
        if not self.allow_provider_mutations:
            raise PermissionError("Provider mutations are disabled")
        _validate_credential_reference(payload.credential_reference)
        provider = ProviderProfile(**payload.model_dump())
        return self.providers.register(provider)

    def update_provider(
        self, provider_id: str, payload: ProviderUpdate
    ) -> ProviderProfile:
        if not self.allow_provider_mutations:
            raise PermissionError("Provider mutations are disabled")
        provider = self.providers.get(provider_id)
        if "credential_reference" in payload.model_fields_set:
            _validate_credential_reference(payload.credential_reference or "")
        updates = {
            key: value
            for key, value in payload.model_dump().items()
            if key in payload.model_fields_set
        }
        updated = provider.model_copy(update={**updates, "updated_at": utc_now()})
        return self.providers.upsert(updated)

    async def health_check(
        self, provider_id: str, model_id: str | None = None
    ) -> tuple[ProviderProfile, ModelProfile, HealthResult]:
        if not self.allow_provider_mutations:
            raise PermissionError("Provider connection tests are disabled")
        provider = self.providers.get(provider_id)
        if model_id:
            model = self.models.get(model_id)
            if model.provider_id != provider_id:
                raise ValueError("model does not belong to the selected Provider")
        else:
            candidates = [
                model
                for model in self.models.for_provider(provider_id)
                if model.enabled
            ]
            if not candidates:
                raise ValueError("Provider has no enabled model to test")
            model = candidates[0]
        adapter = self.adapters.get(provider.provider_type)
        result = await adapter.health_check(provider, model)
        provider = self.providers.upsert(
            provider.model_copy(
                update={"health_status": result.status, "updated_at": utc_now()}
            )
        )
        model = self.models.upsert(
            model.model_copy(update={"health_status": result.status})
        )
        return provider, model, result

    def agent_usage(self, profile: AgentProfile) -> list[ModelUsage]:
        return [
            record
            for record in self.usage.list()
            if record.agent_id in {profile.id, profile.agent_name}
            or record.role_id == profile.role_id
        ]

    def latest_usage(self, profile: AgentProfile) -> ModelUsage | None:
        records = self.agent_usage(profile)
        return max(records, key=lambda item: item.created_at) if records else None

    def latest_route(self, profile: AgentProfile) -> RouteDecisionTrace | None:
        records = [
            trace
            for trace in self.traces.route_decisions()
            if trace.agent_id in {profile.id, profile.agent_name}
            or trace.role_id == profile.role_id
        ]
        return max(records, key=lambda item: item.created_at) if records else None

    def routing_state(self, profile: AgentProfile, policy: RoleModelPolicy) -> str:
        latest = self.latest_usage(profile)
        if latest and latest.fallback_used:
            return "Fallback"
        if (
            len(policy.primary_model_ids) == 1
            and policy.routing_mode.value == "priority"
        ):
            return "Fixed"
        return "Auto"

    def usage_summary(self, profile: AgentProfile) -> dict[str, Any]:
        records = self.agent_usage(profile)
        succeeded = sum(
            record.status is InvocationStatus.SUCCEEDED for record in records
        )
        return {
            "inputTokens": sum(record.input_tokens for record in records),
            "outputTokens": sum(record.output_tokens for record in records),
            "estimatedCost": sum(record.estimated_cost for record in records),
            "successRate": succeeded / len(records) if records else None,
            "invocations": len(records),
        }
