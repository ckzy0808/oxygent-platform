"""Execution, routing, and product-safe workflow traces."""

from datetime import datetime
from enum import Enum
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


class WorkflowPhase(str, Enum):
    REQUIREMENT = "requirement"
    ARCHITECTURE = "architecture"
    PLAN = "plan"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    REVIEW = "review"
    APPROVAL = "approval"


class EngineeringStatus(str, Enum):
    NOT_STARTED = "not-started"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    REVIEWING = "reviewing"
    AWAITING_APPROVAL = "awaiting-approval"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


WORKFLOW_PHASE_ORDER: tuple[WorkflowPhase, ...] = (
    WorkflowPhase.REQUIREMENT,
    WorkflowPhase.ARCHITECTURE,
    WorkflowPhase.PLAN,
    WorkflowPhase.IMPLEMENTATION,
    WorkflowPhase.VERIFICATION,
    WorkflowPhase.REVIEW,
    WorkflowPhase.APPROVAL,
)


class WorkflowEvent(PlatformModel):
    """Unified append-only event contract exposed to product interfaces."""

    event_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    agent_id: str = ""
    role: str = ""
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    phase: WorkflowPhase
    event_type: str = Field(min_length=1)
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowStageProjection(PlatformModel):
    phase: WorkflowPhase
    role: str = ""
    agent_id: str = ""
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    status: EngineeringStatus = EngineeringStatus.NOT_STARTED
    summary: str = ""
    tools_used: list[str] = Field(default_factory=list)
    artifact: dict[str, Any] | None = None
    cost: float = Field(default=0.0, ge=0)
    duration_ms: float = Field(default=0.0, ge=0)
    event_count: int = Field(default=0, ge=0)
    started_at: datetime | None = None
    updated_at: datetime | None = None


class WorkflowRunProjection(PlatformModel):
    run_id: str
    project_id: str
    task_id: str
    name: str
    status: EngineeringStatus
    current_phase: WorkflowPhase | None = None
    total_cost: float = Field(default=0.0, ge=0)
    total_duration_ms: float = Field(default=0.0, ge=0)
    started_at: datetime
    updated_at: datetime
    stages: list[WorkflowStageProjection] = Field(default_factory=list)


def _nonnegative_number(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def project_workflow_run(events: list[WorkflowEvent]) -> WorkflowRunProjection:
    """Project append-only events into a stable seven-phase product view."""
    if not events:
        raise ValueError("workflow run requires at least one event")
    ordered = sorted(events, key=lambda event: (event.timestamp, event.event_id))
    run_id = ordered[0].run_id
    if any(event.run_id != run_id for event in ordered):
        raise ValueError("workflow projection cannot mix run IDs")

    stages: list[WorkflowStageProjection] = []
    for phase in WORKFLOW_PHASE_ORDER:
        phase_events = [event for event in ordered if event.phase is phase]
        if not phase_events:
            stages.append(WorkflowStageProjection(phase=phase))
            continue
        latest = phase_events[-1]
        status_value = latest.payload.get("status", EngineeringStatus.NOT_STARTED.value)
        try:
            stage_status = EngineeringStatus(status_value)
        except ValueError:
            stage_status = EngineeringStatus.NOT_STARTED
        tools: list[str] = []
        for event in phase_events:
            event_tools = event.payload.get("toolsUsed", [])
            if not isinstance(event_tools, list):
                continue
            for tool in event_tools:
                if isinstance(tool, str) and tool not in tools:
                    tools.append(tool)
        summary = next(
            (
                str(event.payload["summary"])
                for event in reversed(phase_events)
                if event.payload.get("summary")
            ),
            "",
        )
        artifact = next(
            (
                event.payload["artifact"]
                for event in reversed(phase_events)
                if isinstance(event.payload.get("artifact"), dict)
            ),
            None,
        )
        stages.append(
            WorkflowStageProjection(
                phase=phase,
                role=latest.role,
                agentId=latest.agent_id,
                providerId=latest.provider_id,
                modelId=latest.model_id,
                status=stage_status,
                summary=summary,
                toolsUsed=tools,
                artifact=artifact,
                cost=sum(
                    _nonnegative_number(event.payload.get("cost"))
                    for event in phase_events
                ),
                durationMs=max(
                    (
                        _nonnegative_number(event.payload.get("durationMs"))
                        for event in phase_events
                    ),
                    default=0.0,
                ),
                eventCount=len(phase_events),
                startedAt=phase_events[0].timestamp,
                updatedAt=latest.timestamp,
            )
        )

    active_stages = [
        stage for stage in stages if stage.status is not EngineeringStatus.NOT_STARTED
    ]
    current = active_stages[-1] if active_stages else None
    status = current.status if current else EngineeringStatus.NOT_STARTED
    if any(stage.status is EngineeringStatus.FAILED for stage in active_stages):
        status = EngineeringStatus.FAILED
    elif any(stage.status is EngineeringStatus.BLOCKED for stage in active_stages):
        status = EngineeringStatus.BLOCKED
    name = next(
        (
            str(event.payload["runName"])
            for event in ordered
            if event.payload.get("runName")
        ),
        f"Workflow run {run_id[:8]}",
    )
    return WorkflowRunProjection(
        runId=run_id,
        projectId=ordered[0].project_id,
        taskId=ordered[0].task_id,
        name=name,
        status=status,
        currentPhase=current.phase if current else None,
        totalCost=sum(stage.cost for stage in stages),
        totalDurationMs=sum(stage.duration_ms for stage in stages),
        startedAt=ordered[0].timestamp,
        updatedAt=ordered[-1].timestamp,
        stages=stages,
    )


class InMemoryExecutionTraceStore:
    """Append-only trace store. Detail dictionaries must never contain secrets."""

    def __init__(self) -> None:
        self._route_decisions: list[RouteDecisionTrace] = []
        self._events: list[ExecutionTrace] = []
        self._workflow_events: list[WorkflowEvent] = []

    def append_route_decision(self, trace: RouteDecisionTrace) -> None:
        self._route_decisions.append(trace)

    def append_event(self, trace: ExecutionTrace) -> None:
        self._events.append(trace)

    def route_decisions(self) -> list[RouteDecisionTrace]:
        return list(self._route_decisions)

    def events(self) -> list[ExecutionTrace]:
        return list(self._events)

    def append_workflow_event(self, event: WorkflowEvent) -> None:
        if any(item.event_id == event.event_id for item in self._workflow_events):
            raise ValueError(f"workflow event already exists: {event.event_id}")
        self._workflow_events.append(event)

    def workflow_events(
        self,
        *,
        run_id: str | None = None,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> list[WorkflowEvent]:
        values = self._workflow_events
        if run_id is not None:
            values = [event for event in values if event.run_id == run_id]
        if project_id is not None:
            values = [event for event in values if event.project_id == project_id]
        if task_id is not None:
            values = [event for event in values if event.task_id == task_id]
        return sorted(values, key=lambda event: (event.timestamp, event.event_id))

    def workflow_runs(
        self,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
    ) -> list[WorkflowRunProjection]:
        events = self.workflow_events(project_id=project_id, task_id=task_id)
        run_ids = list(dict.fromkeys(event.run_id for event in events))
        runs = [
            project_workflow_run([event for event in events if event.run_id == run_id])
            for run_id in run_ids
        ]
        return sorted(runs, key=lambda run: run.updated_at, reverse=True)
