"""Unit tests for append-only Workflow Timeline events and projections."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from oxygent.platform import (
    EngineeringStatus,
    InMemoryExecutionTraceStore,
    WorkflowEvent,
    WorkflowPhase,
    project_workflow_run,
)


def workflow_event(
    event_id: str,
    phase: WorkflowPhase,
    status: EngineeringStatus,
    minute: int,
    **payload,
) -> WorkflowEvent:
    return WorkflowEvent(
        eventId=event_id,
        projectId="project-1",
        taskId="task-1",
        runId="run-1",
        agentId="agent-1",
        role="product_manager",
        providerId="provider-1",
        modelId="model-1",
        phase=phase,
        eventType="phase.updated",
        timestamp=datetime(2026, 7, 20, tzinfo=timezone.utc)
        + timedelta(minutes=minute),
        payload={"status": status.value, **payload},
    )


def test_workflow_event_uses_the_unified_camel_case_contract():
    event = workflow_event(
        "event-1",
        WorkflowPhase.REQUIREMENT,
        EngineeringStatus.ANALYZING,
        0,
    )

    assert set(event.model_dump(by_alias=True)) == {
        "eventId",
        "projectId",
        "taskId",
        "runId",
        "agentId",
        "role",
        "providerId",
        "modelId",
        "phase",
        "eventType",
        "timestamp",
        "payload",
    }
    with pytest.raises(ValidationError):
        WorkflowEvent(
            eventId="event-2",
            projectId="project-1",
            taskId="task-1",
            runId="run-1",
            phase="unknown-phase",
            eventType="phase.started",
        )


def test_event_store_is_append_only_and_filters_without_exposing_internal_lists():
    store = InMemoryExecutionTraceStore()
    event = workflow_event(
        "event-1",
        WorkflowPhase.REQUIREMENT,
        EngineeringStatus.ANALYZING,
        0,
    )
    store.append_workflow_event(event)

    returned = store.workflow_events(run_id="run-1")
    returned.clear()

    assert len(store.workflow_events()) == 1
    with pytest.raises(ValueError, match="already exists"):
        store.append_workflow_event(event)


def test_workflow_projection_orders_all_phases_and_aggregates_stage_metadata():
    events = [
        workflow_event(
            "requirement-started",
            WorkflowPhase.REQUIREMENT,
            EngineeringStatus.ANALYZING,
            0,
            runName="Delivery workflow",
        ),
        workflow_event(
            "requirement-completed",
            WorkflowPhase.REQUIREMENT,
            EngineeringStatus.COMPLETED,
            2,
            summary="Requirements ready",
            toolsUsed=["artifact-read", "artifact-write"],
            cost=0.01,
            durationMs=1200,
            artifact={"id": "artifact-1", "type": "RequirementSpec"},
        ),
        workflow_event(
            "approval-requested",
            WorkflowPhase.APPROVAL,
            EngineeringStatus.AWAITING_APPROVAL,
            3,
            summary="Human approval required",
        ),
    ]

    run = project_workflow_run(list(reversed(events)))

    assert run.name == "Delivery workflow"
    assert run.status is EngineeringStatus.AWAITING_APPROVAL
    assert run.current_phase is WorkflowPhase.APPROVAL
    assert [stage.phase for stage in run.stages] == list(WorkflowPhase)
    requirement = run.stages[0]
    assert requirement.status is EngineeringStatus.COMPLETED
    assert requirement.tools_used == ["artifact-read", "artifact-write"]
    assert requirement.artifact == {"id": "artifact-1", "type": "RequirementSpec"}
    assert requirement.cost == 0.01
    assert requirement.duration_ms == 1200
    assert run.stages[1].status is EngineeringStatus.NOT_STARTED


def test_projection_rejects_mixed_run_ids():
    first = workflow_event(
        "event-1",
        WorkflowPhase.REQUIREMENT,
        EngineeringStatus.ANALYZING,
        0,
    )
    second = first.model_copy(update={"event_id": "event-2", "run_id": "run-2"})

    with pytest.raises(ValueError, match="cannot mix run IDs"):
        project_workflow_run([first, second])
