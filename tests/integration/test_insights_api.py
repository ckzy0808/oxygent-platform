"""Insights API project isolation, labels, ranges, and redaction tests."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from oxygent.platform import (
    InMemoryExecutionTraceStore,
    InMemoryModelUsageStore,
    InvocationStatus,
    ModelProfile,
    ModelRegistry,
    ModelUsage,
    PlatformControlPlane,
    PlatformServices,
    ProjectCreate,
    ProviderProfile,
    ProviderRegistry,
    ProviderType,
    RoleRegistry,
    RouteDecisionTrace,
    build_platform_router,
    default_role_definitions,
)


SECRET = "insights-secret-must-not-leak"
NOW = datetime.now(timezone.utc)


async def insights_services() -> tuple[PlatformServices, str, str]:
    usage = InMemoryModelUsageStore()
    traces = InMemoryExecutionTraceStore()
    control = PlatformControlPlane(
        providers=ProviderRegistry(
            [
                ProviderProfile(
                    id="provider-a",
                    name="Provider A",
                    providerType=ProviderType.OPENAI_COMPATIBLE,
                    baseUrl="https://provider.invalid/v1",
                )
            ]
        ),
        models=ModelRegistry(
            [
                ModelProfile(
                    id="model-a",
                    providerId="provider-a",
                    modelName="model-a",
                    displayName="Model A",
                )
            ]
        ),
        roles=RoleRegistry(default_role_definitions()),
        usage=usage,
        traces=traces,
    )
    services = PlatformServices(control_plane=control)
    project_a = await services.create_project(
        ProjectCreate(name="Project A", settings={"monthlyBudget": 0.01})
    )
    project_b = await services.create_project(ProjectCreate(name="Project B"))
    for project_id, run_id, status in (
        (project_a.id, "run-a", InvocationStatus.SUCCEEDED),
        (project_b.id, "run-b", InvocationStatus.FAILED),
    ):
        usage.append(
            ModelUsage(
                projectId=project_id,
                taskId="task-" + run_id,
                runId=run_id,
                roleId="product_manager",
                agentId="pm-agent",
                providerId="provider-a",
                modelId="model-a",
                inputTokens=120,
                outputTokens=30,
                latencyMs=300,
                estimatedCost=0.012,
                status=status,
                failureReason=SECRET if status is InvocationStatus.FAILED else None,
                createdAt=NOW,
            )
        )
    traces.append_route_decision(
        RouteDecisionTrace(
            id="route-a",
            projectId=project_a.id,
            taskId="task-run-a",
            runId="run-a",
            roleId="product_manager",
            agentId="pm-agent",
            taskType="requirements",
            selectedProviderId="provider-a",
            selectedModelId="model-a",
            selectionReason=f"priority matched; api_key={SECRET}",
        )
    )
    return services, project_a.id, project_b.id


@pytest.mark.asyncio
async def test_insights_summary_and_breakdown_are_project_isolated():
    services, project_a, _ = await insights_services()
    app = FastAPI()
    app.include_router(build_platform_router(services))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        summary = await client.get(
            "/api/v1/platform/insights/summary", params={"projectId": project_a}
        )
        breakdown = await client.get(
            "/api/v1/platform/insights/breakdown",
            params={"projectId": project_a, "dimension": "model"},
        )

    assert summary.status_code == 200
    data = summary.json()["data"]
    assert data["totals"]["invocations"] == 1
    assert data["totals"]["totalTokens"] == 150
    assert data["totals"]["exactInvocations"] == 1
    assert "estimatedCost" not in data["totals"]
    assert "budgets" not in data
    assert len(breakdown.json()["data"]["items"]) == 1
    assert breakdown.json()["data"]["items"][0]["label"] == "Model A"


@pytest.mark.asyncio
async def test_insights_range_is_exclusive_at_end_and_requires_timezone():
    services, project_a, _ = await insights_services()
    app = FastAPI()
    app.include_router(build_platform_router(services))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        excluded = await client.get(
            "/api/v1/platform/insights/summary",
            params={
                "projectId": project_a,
                "dateFrom": (NOW - timedelta(seconds=1)).isoformat(),
                "dateTo": NOW.isoformat(),
            },
        )
        invalid = await client.get(
            "/api/v1/platform/insights/summary",
            params={"dateFrom": "2026-07-01T00:00:00"},
        )

    assert excluded.json()["data"]["totals"]["invocations"] == 0
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_insights_runs_redact_route_and_failure_secrets():
    services, _, _ = await insights_services()
    app = FastAPI()
    app.include_router(build_platform_router(services))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/platform/insights/runs")

    assert response.status_code == 200
    payload = response.json()["data"]
    serialized = json.dumps(payload)
    assert SECRET not in serialized
    assert "[redacted]" in serialized
    failed = next(item for item in payload["items"] if item["status"] == "failed")
    assert failed["failureReason"] == "Provider call failed"
    assert failed["workflowUrl"].startswith("workflows.html?runId=")


@pytest.mark.asyncio
async def test_unknown_project_filter_is_not_found():
    services, _, _ = await insights_services()
    app = FastAPI()
    app.include_router(build_platform_router(services))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/platform/insights/summary",
            params={"projectId": "missing-project"},
        )
    assert response.status_code == 404
