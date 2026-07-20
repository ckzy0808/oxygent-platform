"""Execution and routing decision traces for the platform layer."""

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from .common import PlatformModel, utc_now


class RouteDecisionTrace(PlatformModel):
    id: str
    project_id: str
    task_id: str
    run_id: str
    role_id: str
    agent_id: str
    task_type: str
    selected_provider_id: str
    selected_model_id: str
    selection_reason: str
    fallback_chain: list[str] = Field(default_factory=list)
    excluded_providers: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class ExecutionTrace(PlatformModel):
    id: str
    project_id: str
    task_id: str
    run_id: str
    role_id: str
    agent_id: str
    event_type: str
    status: str
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class InMemoryExecutionTraceStore:
    """Append-only trace store. Detail dictionaries must never contain secrets."""

    def __init__(self) -> None:
        self._route_decisions: list[RouteDecisionTrace] = []
        self._events: list[ExecutionTrace] = []

    def append_route_decision(self, trace: RouteDecisionTrace) -> None:
        self._route_decisions.append(trace)

    def append_event(self, trace: ExecutionTrace) -> None:
        self._events.append(trace)

    def route_decisions(self) -> list[RouteDecisionTrace]:
        return list(self._route_decisions)

    def events(self) -> list[ExecutionTrace]:
        return list(self._events)
