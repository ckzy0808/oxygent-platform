"""API integration tests for Workflow Timeline projections and event safety."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from oxygent.platform import (
    EngineeringStatus,
    HealthStatus,
    InMemoryExecutionTraceStore,
    ModelProfile,
    ModelRegistry,
    PlatformControlPlane,
    PlatformServices,
    ProviderProfile,
    ProviderRegistry,
    ProviderType,
    RoleRegistry,
    WorkflowEvent,
    WorkflowPhase,
    build_platform_router,
    default_role_definitions,
)


SECRET_VALUE = "workflow-secret-must-not-leak"


def configured_services() -> PlatformServices:
    traces = InMemoryExecutionTraceStore()
    timestamp = datetime(2026, 7, 20, tzinfo=timezone.utc)
    traces.append_workflow_event(
        WorkflowEvent(
            eventId="event-started",
            projectId="project-1",
            taskId="task-1",
            runId="run-1",
            agentId="pm-profile",
            role="product_manager",
            providerId="provider-1",
            modelId="model-1",
            phase=WorkflowPhase.REQUIREMENT,
            eventType="phase.started",
            timestamp=timestamp,
            payload={
                "runName": "Production workflow",
                "status": EngineeringStatus.ANALYZING.value,
                "message": f"Authorization: Bearer {SECRET_VALUE}",
                "cost": SECRET_VALUE,
                "apiKey": SECRET_VALUE,
                "privateReasoning": SECRET_VALUE,
            },
        )
    )
    traces.append_workflow_event(
        WorkflowEvent(
            eventId="event-completed",
            projectId="project-1",
            taskId="task-1",
            runId="run-1",
            agentId="pm-profile",
            role="product_manager",
            providerId="provider-1",
            modelId="model-1",
            phase=WorkflowPhase.REQUIREMENT,
            eventType="artifact.created",
            timestamp=timestamp + timedelta(minutes=1),
            payload={
                "status": EngineeringStatus.COMPLETED.value,
                "summary": "RequirementSpec ready",
                "toolsUsed": ["artifact-read"],
                "cost": 0.01,
                "durationMs": 800,
                "artifact": {
                    "id": "artifact-1",
                    "type": "RequirementSpec",
                    "schemaVersion": "1.0",
                    "validationStatus": "valid",
                    "rawContent": SECRET_VALUE,
                },
            },
        )
    )
    traces.append_workflow_event(
        WorkflowEvent(
            eventId="approval-requested",
            projectId="project-1",
            taskId="task-1",
            runId="run-1",
            agentId="human-approval",
            role="approver",
            phase=WorkflowPhase.APPROVAL,
            eventType="approval.requested",
            timestamp=timestamp + timedelta(minutes=2),
            payload={
                "status": EngineeringStatus.AWAITING_APPROVAL.value,
                "summary": "Approval required",
            },
        )
    )
    control = PlatformControlPlane(
        providers=ProviderRegistry(
            [
                ProviderProfile(
                    id="provider-1",
                    name="Provider One",
                    providerType=ProviderType.OPENAI_COMPATIBLE,
                    baseUrl="https://provider.invalid/v1",
                    credentialReference="env:PROVIDER_KEY",
                    healthStatus=HealthStatus.HEALTHY,
                )
            ]
        ),
        models=ModelRegistry(
            [
                ModelProfile(
                    id="model-1",
                    providerId="provider-1",
                    modelName="model-one",
                    displayName="Model One",
                    healthStatus=HealthStatus.HEALTHY,
                )
            ]
        ),
        roles=RoleRegistry(default_role_definitions()),
        traces=traces,
    )
    return PlatformServices(control_plane=control)


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(build_platform_router(configured_services()))
    return app


@pytest.mark.asyncio
async def test_workflow_run_api_projects_seven_phases_and_enriches_names():
    async with AsyncClient(
        transport=ASGITransport(app=build_app()), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/platform/workflows/runs?projectId=project-1&taskId=task-1"
        )

    assert response.status_code == 200
    assert SECRET_VALUE not in response.text
    assert "rawContent" not in response.text
    runs = response.json()["data"]["items"]
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "Production workflow"
    assert run["status"] == "awaiting-approval"
    assert run["currentPhase"] == "approval"
    assert len(run["stages"]) == 7
    requirement = run["stages"][0]
    assert requirement["roleName"] == "Product Manager"
    assert requirement["providerName"] == "Provider One"
    assert requirement["modelName"] == "Model One"
    assert requirement["artifact"]["id"] == "artifact-1"


@pytest.mark.asyncio
async def test_workflow_event_api_exposes_only_sanitized_product_metadata():
    async with AsyncClient(
        transport=ASGITransport(app=build_app()), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/platform/workflows/runs/run-1/events")

    assert response.status_code == 200
    response_text = response.text
    assert SECRET_VALUE not in response_text
    assert "privateReasoning" not in response_text
    assert "apiKey" not in response_text
    assert "rawContent" not in response_text
    events = response.json()["data"]["items"]
    assert events[0]["payload"]["message"].endswith("[redacted]")
    assert events[1]["payload"]["artifact"] == {
        "id": "artifact-1",
        "type": "RequirementSpec",
        "schemaVersion": "1.0",
        "validationStatus": "valid",
    }


@pytest.mark.asyncio
async def test_missing_workflow_run_returns_not_found():
    async with AsyncClient(
        transport=ASGITransport(app=build_app()), base_url="http://test"
    ) as client:
        run_response = await client.get("/api/v1/platform/workflows/runs/missing-run")
        event_response = await client.get(
            "/api/v1/platform/workflows/runs/missing-run/events"
        )

    assert run_response.status_code == 404
    assert event_response.status_code == 404
