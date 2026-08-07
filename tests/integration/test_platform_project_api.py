"""End-to-end API tests for additive Project and Artifact routes."""

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from oxygent.platform import (
    CodeStageRun,
    CodeStageStatus,
    ModelResponse,
    PlatformServices,
    ProjectCreate,
    ProviderType,
    RequirementSpec,
    RequirementSpecContent,
    SourceWorkspaceManager,
    SourceWorkspaceAnalysis,
    WorkflowLaunchRequest,
    build_environment_workflow_bundle,
    build_platform_router,
)


class ProjectAnalysisAdapter:
    async def complete(self, _request):
        return ModelResponse(
            output=(
                '{"summary":"A small Python web service.",'
                '"projectType":"Web API","technologies":["Python"],'
                '"architecture":["HTTP API layer"],"mainFeatures":["Health endpoint"],'
                '"keyFiles":["sample-project/app.py"],"risks":["No tests"],'
                '"suggestedFocus":["Add tests"]}'
            ),
            inputTokens=120,
            outputTokens=80,
            latencyMs=25,
        )

    async def stream(self, _request):
        if False:
            yield None

    async def health_check(self, _provider, _model):
        raise NotImplementedError


class CapturingWorkflowExecutor:
    def __init__(self):
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        return {}


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
            "simpleCodeStage": True,
            "codeStageQualityLifecycle": True,
            "projectFolderImport": True,
            "projectSourceAnalysis": False,
            "codeWorkspace": False,
            "gitWorktrees": False,
            "diffVerification": False,
            "approvalLifecycle": False,
            "agents": True,
            "models": True,
            "workflowTimeline": True,
            "workflowExecution": False,
            "insights": True,
            "executionDrawer": True,
            "controlPlaneConfigured": False,
            "providerMutations": False,
        }
        project = (
            await client.post("/api/v1/platform/projects", json={"name": "Guarded"})
        ).json()["data"]["project"]
        workflow_response = await client.post(
            f"/api/v1/platform/projects/{project['id']}/workflows/runs",
            json={"idea": "This must not silently use mock model output"},
        )
        assert workflow_response.status_code == 409
        assert "not configured" in workflow_response.json()["detail"]
        await client.post(
            f"/api/v1/platform/projects/{project['id']}/tasks/from-chat",
            json={"title": "Keep", "objective": "Prevent destructive deletion"},
        )
        delete_response = await client.delete(
            f"/api/v1/platform/projects/{project['id']}"
        )
        assert delete_response.status_code == 409


@pytest.mark.asyncio
async def test_project_folder_import_creates_a_managed_source_without_path_leak(
    app: FastAPI,
):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        project = (
            await client.post("/api/v1/platform/projects", json={"name": "Uploaded"})
        ).json()["data"]["project"]
        response = await client.post(
            f"/api/v1/platform/projects/{project['id']}/source-workspaces/import",
            data={
                "name": "sample-project",
                "pathsJson": '["sample-project/app.py", "sample-project/README.md"]',
            },
            files=[
                ("files", ("app.py", b"print('ok')\n", "text/x-python")),
                ("files", ("README.md", b"# Sample\n", "text/markdown")),
            ],
        )
        assert response.status_code == 201
        source = response.json()["data"]["sourceWorkspace"]
        assert source["fileCount"] == 2
        assert source["name"] == "sample-project"
        assert "rootPath" not in source

        listed = await client.get(
            f"/api/v1/platform/projects/{project['id']}/source-workspaces"
        )
        assert listed.json()["data"]["items"][0]["id"] == source["id"]
        assert "rootPath" not in listed.json()["data"]["items"][0]


@pytest.mark.asyncio
async def test_code_stage_changes_are_previewable_in_browser(tmp_path: Path):
    manager = SourceWorkspaceManager(tmp_path / "managed-sources")
    services = PlatformServices(source_workspace_manager=manager)
    project = await services.create_project(ProjectCreate(name="Previewable code"))
    source = manager.create(
        project.id,
        "uploaded",
        [("app.py", b"def value():\n    return 1\n")],
    )
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=run_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=run_root,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=run_root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=run_root, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=run_root, check=True)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=run_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (run_root / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    (run_root / "feature.py").write_text("ENABLED = True\n", encoding="utf-8")
    run = CodeStageRun(
        projectId=project.id,
        sourceWorkspaceId=source.id,
        instructions="Change the value",
        status=CodeStageStatus.COMPLETED,
        changedFiles=["app.py", "feature.py"],
        baseCommit=base_commit,
        runPath=str(run_root),
    )
    await services.code_stage_runs.create(run)
    application = FastAPI()
    application.include_router(build_platform_router(services))

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://127.0.0.1"
    ) as client:
        changes_response = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/changes"
        )
        assert changes_response.status_code == 200
        changes = changes_response.json()["data"]["changes"]
        assert changes["changedFiles"] == ["app.py", "feature.py"]
        assert changes["additions"] == 2
        assert changes["deletions"] == 1

        file_response = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/changes/app.py"
        )
        assert file_response.status_code == 200
        change = file_response.json()["data"]["change"]
        assert change["changeType"] == "modified"
        assert "return 1" in change["beforeContent"]
        assert "return 2" in change["afterContent"]

        added_response = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-stage-runs/{run.id}/changes/feature.py"
        )
        added = added_response.json()["data"]["change"]
        assert added["changeType"] == "added"
        assert added["beforeContent"] == ""
        assert "ENABLED = True" in added["afterContent"]


@pytest.mark.asyncio
async def test_imported_project_can_be_analyzed_and_usage_is_recorded(tmp_path):
    environment = {
        "OXYGENT_SHARED_PROVIDER_ID": "shared-test",
        "OXYGENT_SHARED_PROVIDER_TYPE": "openai-compatible",
        "OXYGENT_SHARED_BASE_URL": "https://example.invalid/v1",
        "OXYGENT_SHARED_CREDENTIAL_REFERENCE": "env:TEST_API_KEY",
        "OXYGENT_SHARED_MODEL": "test-model",
        "OXYGENT_REVIEWER_EXCLUDE_PRODUCER_PROVIDER": "0",
    }
    bundle = build_environment_workflow_bundle(environment=environment)
    bundle.control_plane.adapters.register(
        ProviderType.OPENAI_COMPATIBLE, ProjectAnalysisAdapter()
    )
    services = PlatformServices(
        control_plane=bundle.control_plane,
        artifacts=bundle.artifacts,
        source_workspace_manager=SourceWorkspaceManager(tmp_path / "sources"),
    )
    application = FastAPI()
    application.include_router(build_platform_router(services))

    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://127.0.0.1"
    ) as client:
        project = (
            await client.post("/api/v1/platform/projects", json={"name": "Existing"})
        ).json()["data"]["project"]
        imported = await client.post(
            f"/api/v1/platform/projects/{project['id']}/source-workspaces/import",
            data={
                "name": "sample-project",
                "pathsJson": '["sample-project/app.py", "sample-project/README.md"]',
            },
            files=[
                ("files", ("app.py", b"def health(): return 'ok'\n", "text/x-python")),
                ("files", ("README.md", b"# Web service\n", "text/markdown")),
            ],
        )
        source = imported.json()["data"]["sourceWorkspace"]
        analyzed = await client.post(
            f"/api/v1/platform/projects/{project['id']}/source-workspaces/{source['id']}/analyze"
        )

        assert analyzed.status_code == 201
        analysis = analyzed.json()["data"]["analysis"]
        assert analysis["summary"] == "A small Python web service."
        assert analysis["technologies"] == ["Python"]
        assert "rootPath" not in analysis
        listed = await client.get(
            f"/api/v1/platform/projects/{project['id']}/source-analyses"
        )
        assert listed.json()["data"]["items"][0]["id"] == analysis["id"]
        usage = services.control_plane.usage.list()
        assert len(usage) == 1
        assert usage[0].role_id == "product_manager"
        assert usage[0].input_tokens == 120


@pytest.mark.asyncio
async def test_existing_project_analysis_is_added_to_requirement_workflow_context():
    executor = CapturingWorkflowExecutor()
    services = PlatformServices(workflow_executor=executor)
    project = await services.create_project(ProjectCreate(name="Existing"))
    analysis = await services.source_analyses.create(
        SourceWorkspaceAnalysis(
            projectId=project.id,
            sourceWorkspaceId="source-1",
            summary="Existing Python service with an HTTP API.",
            projectType="Web API",
            technologies=["Python"],
            architecture=["Layered service"],
            mainFeatures=["Health endpoint"],
            risks=["No tests"],
            providerId="provider-1",
            modelId="model-1",
        )
    )

    run_id = await services.start_role_workflow(
        project.id,
        WorkflowLaunchRequest(
            idea="Add authentication and tests.",
            sourceWorkspaceId="source-1",
            sourceAnalysisId=analysis.id,
        ),
    )
    await services.wait_for_workflow(run_id)

    assert len(executor.requests) == 1
    prompt = executor.requests[0].idea
    assert "Existing Python service" in prompt
    assert "Add authentication and tests" in prompt
    assert "greenfield" in prompt
