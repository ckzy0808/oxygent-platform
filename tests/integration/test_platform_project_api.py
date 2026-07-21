"""End-to-end API tests for additive Project and Artifact routes."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from oxygent.platform import (
    PlatformServices,
    RequirementSpec,
    RequirementSpecContent,
    build_platform_router,
)


@pytest.fixture
def services() -> PlatformServices:
    return PlatformServices()


@pytest.fixture
def app(services: PlatformServices) -> FastAPI:
    application = FastAPI()
    application.include_router(build_platform_router(services))
    return application


@pytest.mark.asyncio
async def test_project_api_create_list_update_and_chat_conversion(
    app: FastAPI, services: PlatformServices
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/api/v1/platform/projects",
            json={
                "name": "API Project",
                "description": "Project API integration",
                "repository": "repository-reference",
                "team": ["Product Manager", "Reviewer"],
                "settings": {},
            },
        )
        assert created.status_code == 201
        project = created.json()["data"]["project"]

        source = services.artifacts.append(
            RequirementSpec(
                id="api-requirement",
                projectId=project["id"],
                taskId="workflow-task",
                producerRole="product_manager",
                producerAgent="pm_agent",
                providerId="provider-a",
                modelId="model-a",
                content=RequirementSpecContent(summary="API requirement"),
            )
        )
        task_response = await client.post(
            f"/api/v1/platform/projects/{project['id']}/tasks/from-chat",
            json={
                "title": "Trace-linked task",
                "objective": "Create a task without copying the transcript",
                "sourceTraceId": "trace-api",
                "attachmentReferences": ["safe_upload.pdf"],
                "sourceArtifactIds": [source.id],
            },
        )
        assert task_response.status_code == 201
        task = task_response.json()["data"]["task"]
        assert task["sourceTraceId"] == "trace-api"
        assert "transcript" not in task

        projects = await client.get("/api/v1/platform/projects")
        assert projects.json()["data"]["items"][0]["activeTasks"] == 1

        updated = await client.patch(
            f"/api/v1/platform/projects/{project['id']}",
            json={"description": "Updated through API"},
        )
        assert updated.json()["data"]["project"]["description"] == "Updated through API"

        tasks = await client.get(f"/api/v1/platform/projects/{project['id']}/tasks")
        assert [item["id"] for item in tasks.json()["data"]["items"]] == [task["id"]]


@pytest.mark.asyncio
async def test_artifact_api_preserves_revisions_and_project_isolation(
    app: FastAPI, services: PlatformServices
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = (
            await client.post("/api/v1/platform/projects", json={"name": "First"})
        ).json()["data"]["project"]
        second = (
            await client.post("/api/v1/platform/projects", json={"name": "Second"})
        ).json()["data"]["project"]
        original = services.artifacts.append(
            RequirementSpec(
                id="revision-source",
                projectId=first["id"],
                taskId="workflow-task",
                producerRole="product_manager",
                producerAgent="pm_agent",
                providerId="provider-a",
                modelId="model-a",
                content=RequirementSpecContent(summary="Version one"),
            )
        )

        revision_response = await client.post(
            f"/api/v1/platform/projects/{first['id']}/artifacts/{original.id}/revisions",
            json={
                "content": {"summary": "Version two"},
                "producerRole": "product_manager",
                "producerAgent": "pm_agent",
                "providerId": "provider-b",
                "modelId": "model-b",
                "validationStatus": "valid",
            },
        )
        assert revision_response.status_code == 201
        revision = revision_response.json()["data"]["artifact"]
        assert revision["revision"] == 2
        assert revision["supersedesArtifactId"] == original.id

        all_artifacts = await client.get(
            f"/api/v1/platform/projects/{first['id']}/artifacts"
        )
        assert len(all_artifacts.json()["data"]["items"]) == 2
        latest = await client.get(
            f"/api/v1/platform/projects/{first['id']}/artifacts?latestOnly=true"
        )
        assert [item["id"] for item in latest.json()["data"]["items"]] == [
            revision["id"]
        ]

        isolated = await client.get(
            f"/api/v1/platform/projects/{second['id']}/artifacts/{original.id}"
        )
        assert isolated.status_code == 404


@pytest.mark.asyncio
async def test_capabilities_and_nonempty_project_delete_guard(app: FastAPI):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        capabilities = await client.get("/api/v1/platform/capabilities")
        assert capabilities.json()["data"]["capabilities"] == {
            "projects": True,
            "artifacts": True,
            "chatToProjectTask": True,
            "codeWorkspace": False,
            "gitWorktrees": False,
            "diffVerification": False,
            "approvalLifecycle": False,
            "agents": True,
            "models": True,
            "workflowTimeline": True,
            "insights": True,
            "executionDrawer": True,
            "controlPlaneConfigured": False,
            "providerMutations": False,
        }
        project = (
            await client.post("/api/v1/platform/projects", json={"name": "Guarded"})
        ).json()["data"]["project"]
        await client.post(
            f"/api/v1/platform/projects/{project['id']}/tasks/from-chat",
            json={"title": "Keep", "objective": "Prevent destructive deletion"},
        )
        delete_response = await client.delete(
            f"/api/v1/platform/projects/{project['id']}"
        )
        assert delete_response.status_code == 409
