"""Deterministic role-aware model routing and OxyGent LLM integration."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from oxygent.config import Config
from oxygent.oxy.llms.base_llm import BaseLLM
from oxygent.schemas import OxyRequest, OxyResponse, OxyState, TokenUsage
from oxygent.utils.common_utils import generate_uuid
from oxygent.utils.token_utils import build_token_usage

from .common import PlatformModel, utc_now
from .profiles import (
    AgentProfile,
    HealthStatus,
    ModelProfile,
    ProviderProfile,
    RoleDefinition,
    RoleModelPolicy,
    RoutingMode,
)
from .provider_adapters import (
    ModelRequest,
    ModelResponse,
)
from .registries import (
    ModelRegistry,
    ProviderRegistry,
)
from .tracing import ExecutionTrace, RouteDecisionTrace
from .usage import InMemoryModelUsageStore, InvocationStatus, ModelUsage

logger = logging.getLogger(__name__)


class ModelRoutingError(RuntimeError):
    pass


class RoutingContext(PlatformModel):
    project_id: str = ""
    task_id: str = ""
    run_id: str = ""
    role_id: str = ""
    agent_id: str = ""
    policy_id: str = ""
    task_type: str = "general"
    complexity: str = "medium"
    risk: str = "medium"
    required_capabilities: set[str] = Field(default_factory=set)
    budget: Optional[float] = Field(default=None, ge=0)
    excluded_providers: set[str] = Field(default_factory=set)
    provider_health: dict[str, HealthStatus] = Field(default_factory=dict)
    historical_success_rate: dict[str, float] = Field(default_factory=dict)
    producer_provider_id: Optional[str] = None
    estimated_input_tokens: int = Field(default=0, ge=0)
    estimated_output_tokens: int = Field(default=0, ge=0)


class ModelRouteDecision(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    provider_id: str
    model_profile_id: str
    selection_reason: str
    fallback_chain: list[str] = Field(default_factory=list)
    selected_from_fallback: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class ModelRoutingEngine:
    """Rule router with no statistical or machine-learning model."""

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        usage_store: InMemoryModelUsageStore,
    ) -> None:
        self.provider_registry = provider_registry
        self.model_registry = model_registry
        self.usage_store = usage_store

    @staticmethod
    def _estimated_cost(
        model: ModelProfile, input_tokens: int, output_tokens: int
    ) -> float:
        return (
            input_tokens * model.input_cost_per_million
            + output_tokens * model.output_cost_per_million
        ) / 1_000_000

    def _eligible(
        self,
        model_id: str,
        policy: RoleModelPolicy,
        context: RoutingContext,
        role: RoleDefinition,
    ) -> tuple[ModelProfile, ProviderProfile] | None:
        model = self.model_registry.get(model_id)
        provider = self.provider_registry.get(model.provider_id)
        excluded = set(policy.excluded_providers) | set(context.excluded_providers)
        if policy.exclude_same_provider_as_producer and context.producer_provider_id:
            excluded.add(context.producer_provider_id)

        if not model.enabled or not provider.enabled or provider.id in excluded:
            return None
        provider_health = context.provider_health.get(
            provider.id, provider.health_status
        )
        if provider_health is HealthStatus.UNAVAILABLE:
            return None
        if model.health_status is HealthStatus.UNAVAILABLE:
            return None

        required = set(policy.required_capabilities) | set(
            context.required_capabilities
        )
        if not required.issubset(model.capabilities):
            return None
        if (
            policy.max_latency is not None
            and model.expected_latency_ms is not None
            and model.expected_latency_ms > policy.max_latency
        ):
            return None

        budgets = [
            value
            for value in (policy.max_cost_per_run, context.budget)
            if value is not None
        ]
        effective_budget = min(budgets) if budgets else None
        if effective_budget is not None:
            estimate = self._estimated_cost(
                model,
                context.estimated_input_tokens,
                context.estimated_output_tokens,
            )
            if estimate > effective_budget:
                return None
        return model, provider

    def _sort(
        self,
        candidates: list[tuple[ModelProfile, ProviderProfile]],
        policy: RoleModelPolicy,
        context: RoutingContext,
    ) -> list[tuple[ModelProfile, ProviderProfile]]:
        if policy.routing_mode is RoutingMode.PRIORITY:
            return candidates

        def success_rate(model: ModelProfile) -> float:
            return context.historical_success_rate.get(
                model.id, self.usage_store.historical_success_rate(model.id)
            )

        if policy.routing_mode is RoutingMode.LOWEST_COST:
            return sorted(
                candidates, key=lambda item: (item[0].cost_tier, -success_rate(item[0]))
            )
        if policy.routing_mode is RoutingMode.LOWEST_LATENCY:
            return sorted(
                candidates,
                key=lambda item: (item[0].latency_tier, -success_rate(item[0])),
            )
        return sorted(
            candidates,
            key=lambda item: (
                -success_rate(item[0]),
                item[0].cost_tier,
                item[0].latency_tier,
            ),
        )

    def route(
        self,
        role: RoleDefinition,
        policy: RoleModelPolicy,
        context: RoutingContext,
    ) -> ModelRouteDecision:
        primary = [
            eligible
            for model_id in policy.primary_model_ids
            if (eligible := self._eligible(model_id, policy, context, role))
        ]
        fallback = [
            eligible
            for model_id in policy.fallback_model_ids
            if (eligible := self._eligible(model_id, policy, context, role))
        ]
        primary = self._sort(primary, policy, context)
        fallback = self._sort(fallback, policy, context)

        selected_from_fallback = not primary
        candidates = primary + fallback
        if not candidates:
            raise ModelRoutingError(
                f"no eligible model for role={role.id} task_type={context.task_type}"
            )
        selected_model, selected_provider = candidates[0]
        remaining = [model.id for model, _provider in candidates[1:]]
        source = "fallback" if selected_from_fallback else "primary"
        success_rate = context.historical_success_rate.get(
            selected_model.id,
            self.usage_store.historical_success_rate(selected_model.id),
        )
        reason = (
            f"selected {source} model by {policy.routing_mode.value}; "
            f"capabilities={sorted(selected_model.capabilities)}; "
            f"historical_success_rate={success_rate:.3f}; "
            f"complexity={context.complexity}; risk={context.risk}"
        )
        return ModelRouteDecision(
            provider_id=selected_provider.id,
            model_profile_id=selected_model.id,
            selection_reason=reason,
            fallback_chain=remaining,
            selected_from_fallback=selected_from_fallback,
        )


class ModelRouter(BaseLLM):
    """BaseLLM-compatible router that delegates to provider adapters."""

    model_name: str = Field(default="model-router")
    default_policy_id: str = ""
    is_send_think: bool = False
    provider_registry: Any = Field(exclude=True, repr=False)
    model_registry: Any = Field(exclude=True, repr=False)
    role_registry: Any = Field(exclude=True, repr=False)
    policy_registry: Any = Field(exclude=True, repr=False)
    agent_profile_registry: Any = Field(exclude=True, repr=False)
    adapter_registry: Any = Field(exclude=True, repr=False)
    usage_store: Any = Field(exclude=True, repr=False)
    trace_store: Any = Field(exclude=True, repr=False)

    def _resolve_policy(
        self, context: RoutingContext
    ) -> tuple[RoleDefinition, RoleModelPolicy, AgentProfile | None]:
        agent_profile = None
        policy_id = context.policy_id or self.default_policy_id
        if context.agent_id and self.agent_profile_registry.has(context.agent_id):
            agent_profile = self.agent_profile_registry.get(context.agent_id)
            policy_id = policy_id or agent_profile.model_policy_id
            context.role_id = context.role_id or agent_profile.role_id
        if policy_id:
            policy = self.policy_registry.get(policy_id)
        else:
            policy = self.policy_registry.for_role(context.role_id)
        role = self.role_registry.get(context.role_id or policy.role_id)
        return role, policy, agent_profile

    @staticmethod
    def _estimated_cost(
        model: ModelProfile, input_tokens: int, output_tokens: int
    ) -> float:
        return (
            input_tokens * model.input_cost_per_million
            + output_tokens * model.output_cost_per_million
        ) / 1_000_000

    def _usage(
        self,
        context: RoutingContext,
        model: ModelProfile,
        response: ModelResponse | None,
        latency_ms: float,
        status: InvocationStatus,
        fallback_used: bool,
        failure_reason: str | None = None,
    ) -> ModelUsage:
        input_tokens = response.input_tokens if response else 0
        output_tokens = response.output_tokens if response else 0
        return ModelUsage(
            project_id=context.project_id,
            task_id=context.task_id,
            run_id=context.run_id,
            role_id=context.role_id,
            agent_id=context.agent_id,
            provider_id=model.provider_id,
            model_id=model.id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            token_count_method=(
                response.token_count_method
                if response
                else TokenUsage().estimation_method
            ),
            invocation_type="workflow",
            latency_ms=max(latency_ms, 0),
            estimated_cost=self._estimated_cost(model, input_tokens, output_tokens),
            status=status,
            failure_reason=failure_reason,
            fallback_used=fallback_used,
        )

    async def _execute(self, oxy_request: OxyRequest) -> OxyResponse:
        messages = await self._get_messages(oxy_request)
        raw_context = oxy_request.arguments.get("_routing_context", {})
        context = (
            raw_context
            if isinstance(raw_context, RoutingContext)
            else RoutingContext.model_validate(raw_context)
        )
        context.run_id = (
            context.run_id or oxy_request.current_trace_id or generate_uuid()
        )
        context.task_id = context.task_id or oxy_request.node_id or generate_uuid()
        context.agent_id = context.agent_id or oxy_request.caller or ""

        role, policy, _agent_profile = self._resolve_policy(context)
        context.role_id = context.role_id or policy.role_id
        engine = ModelRoutingEngine(
            self.provider_registry, self.model_registry, self.usage_store
        )
        decision = engine.route(role, policy, context)
        self.trace_store.append_route_decision(
            RouteDecisionTrace(
                id=decision.id,
                project_id=context.project_id,
                task_id=context.task_id,
                run_id=context.run_id,
                role_id=context.role_id,
                agent_id=context.agent_id,
                task_type=context.task_type,
                selected_provider_id=decision.provider_id,
                selected_model_id=decision.model_profile_id,
                selection_reason=decision.selection_reason,
                fallback_chain=decision.fallback_chain,
                excluded_providers=sorted(context.excluded_providers),
                required_capabilities=sorted(context.required_capabilities),
            )
        )

        parameters = Config.get_llm_config(exclude=["semaphore", "timeout"])
        parameters.update(self.llm_params)
        parameters.update(
            {
                key: value
                for key, value in oxy_request.arguments.items()
                if key not in {"messages", "_routing_context"}
            }
        )

        model_chain = [decision.model_profile_id, *decision.fallback_chain]
        last_failure = "ModelRoutingError"
        for attempt, model_id in enumerate(model_chain):
            model = self.model_registry.get(model_id)
            provider = self.provider_registry.get(model.provider_id)
            adapter = self.adapter_registry.get(provider.provider_type)
            started = time.perf_counter()
            fallback_used = decision.selected_from_fallback or attempt > 0
            try:
                model_response = await adapter.complete(
                    ModelRequest(
                        provider=provider,
                        model=model,
                        messages=messages,
                        parameters=parameters,
                        transport_request=oxy_request,
                    )
                )
                elapsed_ms = (time.perf_counter() - started) * 1000
                usage = self._usage(
                    context,
                    model,
                    model_response,
                    model_response.latency_ms or elapsed_ms,
                    InvocationStatus.SUCCEEDED,
                    fallback_used,
                )
                self.usage_store.append(usage)
                self.trace_store.append_event(
                    ExecutionTrace(
                        id=generate_uuid(),
                        project_id=context.project_id,
                        task_id=context.task_id,
                        run_id=context.run_id,
                        role_id=context.role_id,
                        agent_id=context.agent_id,
                        event_type="model_invocation",
                        status="succeeded",
                        provider_id=provider.id,
                        model_id=model.id,
                        details={"fallbackUsed": fallback_used, "attempt": attempt + 1},
                    )
                )
                token_usage = TokenUsage(
                    input_tokens=model_response.input_tokens,
                    output_tokens=model_response.output_tokens,
                    model_name=model.model_name,
                    estimation_method=model_response.token_count_method,
                )
                if token_usage.total_tokens == 0:
                    token_usage = build_token_usage(
                        None, messages, model_response.output, model.model_name
                    )
                return OxyResponse(
                    state=OxyState.COMPLETED,
                    output=model_response.output,
                    extra={
                        "usage": token_usage,
                        "provider_id": provider.id,
                        "model_id": model.id,
                        "model_profile_id": model.id,
                        "selection_reason": decision.selection_reason,
                        "fallback_chain": decision.fallback_chain,
                        "fallback_used": fallback_used,
                        "route_decision_id": decision.id,
                    },
                )
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                last_failure = type(exc).__name__
                self.usage_store.append(
                    self._usage(
                        context,
                        model,
                        None,
                        elapsed_ms,
                        InvocationStatus.FAILED,
                        fallback_used,
                        failure_reason=last_failure,
                    )
                )
                self.trace_store.append_event(
                    ExecutionTrace(
                        id=generate_uuid(),
                        project_id=context.project_id,
                        task_id=context.task_id,
                        run_id=context.run_id,
                        role_id=context.role_id,
                        agent_id=context.agent_id,
                        event_type="model_invocation",
                        status="failed",
                        provider_id=provider.id,
                        model_id=model.id,
                        details={"failureType": last_failure, "attempt": attempt + 1},
                    )
                )
                logger.warning(
                    "model invocation failed; provider_id=%s model_id=%s error_type=%s",
                    provider.id,
                    model.id,
                    last_failure,
                    extra={
                        "trace_id": oxy_request.current_trace_id,
                        "node_id": oxy_request.node_id,
                    },
                )

        raise ModelRoutingError(
            f"all routed models failed for role={context.role_id}; failure_type={last_failure}"
        )
