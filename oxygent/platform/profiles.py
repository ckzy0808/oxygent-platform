"""Provider, model, role, agent, and policy domain profiles."""

from enum import Enum
from datetime import datetime
from typing import Optional

from pydantic import Field, model_validator

from .common import PlatformModel, utc_now


class ProviderType(str, Enum):
    """Protocols supported now, plus reserved native protocol identifiers."""

    OPENAI_COMPATIBLE = "openai-compatible"
    GEMINI = "gemini"
    OLLAMA = "ollama"
    OPENAI_NATIVE = "openai-native"
    ANTHROPIC_NATIVE = "anthropic-native"


class HealthStatus(str, Enum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class RoutingMode(str, Enum):
    PRIORITY = "priority"
    BALANCED = "balanced"
    LOWEST_COST = "lowest-cost"
    LOWEST_LATENCY = "lowest-latency"


class ProviderProfile(PlatformModel):
    """Non-secret Provider configuration.

    ``credential_reference`` is an opaque reference such as ``env:PM_API_KEY``.
    The resolved credential is never stored on this model.
    """

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    provider_type: ProviderType
    base_url: str = Field(min_length=1)
    credential_reference: str = ""
    enabled: bool = True
    timeout: float = Field(default=300.0, gt=0)
    health_status: HealthStatus = HealthStatus.UNKNOWN
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ModelProfile(PlatformModel):
    """A model exposed by a ProviderProfile."""

    id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    capabilities: set[str] = Field(default_factory=set)
    context_window: int = Field(default=0, ge=0)
    cost_tier: int = Field(default=1, ge=0)
    latency_tier: int = Field(default=1, ge=0)
    enabled: bool = True
    health_status: HealthStatus = HealthStatus.UNKNOWN
    expected_latency_ms: Optional[float] = Field(default=None, ge=0)
    input_cost_per_million: float = Field(default=0.0, ge=0)
    output_cost_per_million: float = Field(default=0.0, ge=0)


class RoleDefinition(PlatformModel):
    """A professional role, deliberately independent from any model name."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    output_artifact_types: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ToolPolicy(PlatformModel):
    """Name-level tool policy that can later be backed by capability grants."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)
    approval_required_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tool_sets(self) -> "ToolPolicy":
        overlap = set(self.allowed_tools) & set(self.denied_tools)
        if overlap:
            raise ValueError(
                f"tools cannot be both allowed and denied: {sorted(overlap)}"
            )
        return self


class AgentProfile(PlatformModel):
    """Platform identity for an existing OxyGent Agent instance."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    model_policy_id: str = Field(min_length=1)
    tool_policy_id: str = Field(min_length=1)
    prompt_key: str = Field(min_length=1)
    enabled: bool = True


class RoleModelPolicy(PlatformModel):
    """Ordered, rule-based model routing policy for one role."""

    id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    routing_mode: RoutingMode = RoutingMode.PRIORITY
    primary_model_ids: list[str] = Field(min_length=1)
    fallback_model_ids: list[str] = Field(default_factory=list)
    required_capabilities: set[str] = Field(default_factory=set)
    excluded_providers: set[str] = Field(default_factory=set)
    max_cost_per_run: Optional[float] = Field(default=None, ge=0)
    max_latency: Optional[float] = Field(default=None, ge=0)
    exclude_same_provider_as_producer: bool = False

    @model_validator(mode="after")
    def validate_model_chain(self) -> "RoleModelPolicy":
        primary = set(self.primary_model_ids)
        overlap = primary & set(self.fallback_model_ids)
        if overlap:
            raise ValueError(
                f"models cannot be both primary and fallback: {sorted(overlap)}"
            )
        if len(primary) != len(self.primary_model_ids):
            raise ValueError("primary_model_ids must not contain duplicates")
        if len(set(self.fallback_model_ids)) != len(self.fallback_model_ids):
            raise ValueError("fallback_model_ids must not contain duplicates")
        return self


def default_role_definitions() -> list[RoleDefinition]:
    """Return the four role templates supported by the first platform phase."""
    return [
        RoleDefinition(
            id="product_manager",
            name="Product Manager",
            responsibilities=["clarify product intent", "define requirements"],
            output_artifact_types=["RequirementSpec"],
        ),
        RoleDefinition(
            id="solution_architect",
            name="Solution Architect",
            responsibilities=["make architecture decisions", "identify constraints"],
            output_artifact_types=["ArchitectureDecision"],
        ),
        RoleDefinition(
            id="technical_lead",
            name="Technical Lead",
            responsibilities=[
                "decompose implementation work",
                "define task dependencies",
            ],
            output_artifact_types=["TaskGraph"],
        ),
        RoleDefinition(
            id="reviewer",
            name="Reviewer",
            responsibilities=["independently review upstream artifacts"],
            output_artifact_types=["ReviewReport"],
        ),
    ]
