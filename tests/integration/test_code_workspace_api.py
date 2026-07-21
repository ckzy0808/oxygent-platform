"""Code Workspace API isolation and authorization tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from oxygent.platform import PlatformServices, ProjectCreate, build_platform_router


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def code_app(tmp_path: Path) -> tuple[FastAPI, PlatformServices, Path, str]:
    repository = tmp_path / "source"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.email", "test@example.invalid")
    git(repository, "config", "user.name", "OxyGent Test")
    (repository / "src").mkdir()
    (repository / "src" / "main.py").write_text("print('safe')\n")
    (repository / ".env").write_text("API_KEY=never-return-this\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "initial")
    services = PlatformServices.with_code_workspace(
        repository_roots={"approved": repository},
        workspace_root=tmp_path / "worktrees",
    )
    app = FastAPI()
    app.include_router(build_platform_router(services))
    return app, services, repository, "approved"


@pytest.mark.asyncio
async def test_repository_and_code_task_api_create_real_isolated_worktree(code_app):
    app, services, source, reference = code_app
    project = await services.create_project(ProjectCreate(name="API Project"))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        sources = await client.get("/api/v1/platform/code/repository-sources")
        assert sources.status_code == 200
        assert sources.json()["data"]["items"] == [
            {"reference": reference, "name": "source"}
        ]
        assert str(source) not in sources.text

        registered = await client.post(
            f"/api/v1/platform/projects/{project.id}/repositories",
            json={
                "name": "API Repository",
                "rootReference": reference,
                "defaultBranch": "main",
                "allowedBaseBranches": ["main"],
            },
        )
        assert registered.status_code == 201
        repository_id = registered.json()["data"]["repository"]["id"]
        created = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-tasks",
            json={
                "repositoryId": repository_id,
                "baseBranch": "main",
                "changeContract": {
                    "objective": "Read repository safely",
                    "acceptanceCriteria": ["Source is unchanged"],
                    "allowedPaths": ["**"],
                    "forbiddenPaths": [],
                    "maxChangedFiles": 10,
                    "maxDiffLines": 100,
                    "dependencyChangesAllowed": False,
                    "risk": "medium",
                },
            },
        )
        assert created.status_code == 201
        task = created.json()["data"]["task"]
        assert Path(task["worktreePath"]) != source
        assert git(source, "status", "--short") == ""

        tree = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task['id']}/repository/tree"
        )
        assert "src/main.py" in tree.json()["data"]["result"]["data"]["files"]
        assert ".env" not in tree.text
        denied = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task['id']}/repository/file",
            params={"path": "../.env"},
        )
        assert denied.status_code == 422


@pytest.mark.asyncio
async def test_code_api_rejects_non_loopback_without_authorization(code_app):
    app, _, _, _ = code_app
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.8", 4312)),
        base_url="http://example.test",
    ) as client:
        response = await client.get("/api/v1/platform/code/repository-sources")
    assert response.status_code == 403
    assert "loopback" in response.json()["detail"]
