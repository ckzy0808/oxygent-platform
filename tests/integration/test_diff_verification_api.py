"""Diff and Verification HTTP contract tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from oxygent.platform import (
    ChangeContract,
    CodeTaskCreate,
    PlatformServices,
    ProjectCreate,
    RepositoryRegistration,
    VerificationCommand,
    VerificationProfileCreate,
    build_platform_router,
)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.mark.asyncio
async def test_diff_and_verification_api_exposes_real_results(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "OxyGent Test")
    (source / "src").mkdir()
    (source / "src" / "app.py").write_text("VALUE = '<old>'\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    services = PlatformServices.with_code_workspace(
        repository_roots={"approved": source},
        workspace_root=tmp_path / "worktrees",
        verification_executables={sys.executable},
    )
    project = await services.create_project(ProjectCreate(name="API Verification"))
    repository = await services.register_repository(
        project.id,
        RepositoryRegistration(
            name="Repository",
            rootReference="approved",
            defaultBranch="main",
            allowedBaseBranches=["main"],
        ),
    )
    command = VerificationCommand(
        id="api-check",
        name="API check",
        slot="unit",
        argv=[sys.executable, "-c", "print('verified <safely>')"],
    )
    profile = await services.register_verification_profile(
        project.id,
        VerificationProfileCreate(
            repositoryId=repository.id, name="API checks", commands=[command]
        ),
    )
    task = await services.create_code_task(
        project.id,
        CodeTaskCreate(
            repositoryId=repository.id,
            changeContract=ChangeContract(
                objective="Verify API",
                acceptanceCriteria=["Real exit code is displayed"],
                allowedPaths=["src/**"],
                maxChangedFiles=5,
                maxDiffLines=50,
                verificationProfileId=profile.id,
            ),
        ),
    )
    (Path(task.worktree_path) / "src" / "app.py").write_text("VALUE = '<script>'\n")
    app = FastAPI()
    app.include_router(build_platform_router(services))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        diff_response = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/diff"
        )
        assert diff_response.status_code == 200
        diff = diff_response.json()["data"]["diff"]
        assert diff["changedFiles"] == ["src/app.py"]
        assert "+VALUE = '<script>'" in diff["diff"]
        assert diff["scopeStatus"] == "valid"

        profile_response = await client.get(
            f"/api/v1/platform/projects/{project.id}/verification-profiles"
        )
        returned = profile_response.json()["data"]["items"][0]
        assert returned["commands"][0]["argv"] == command.argv

        run_response = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/verification-runs",
            json={"profileId": profile.id, "commandId": command.id},
        )
        assert run_response.status_code == 201
        run = run_response.json()["data"]["run"]
        assert run["status"] == "passed"
        assert run["exitCode"] == 0
        assert run["argv"] == command.argv
        assert "verified <safely>" in run["stdoutPreview"]

        output_response = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/verification-outputs/{run['stdoutArtifactId']}"
        )
        assert output_response.status_code == 200
        assert (
            output_response.json()["data"]["output"]["content"].strip()
            == "verified <safely>"
        )


@pytest.mark.asyncio
async def test_diff_api_withholds_content_when_scope_limit_fails(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "OxyGent Test")
    (source / "safe.py").write_text("SAFE = True\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    services = PlatformServices.with_code_workspace(
        repository_roots={"approved": source},
        workspace_root=tmp_path / "worktrees",
    )
    project = await services.create_project(ProjectCreate(name="Blocked Diff"))
    repository = await services.register_repository(
        project.id,
        RepositoryRegistration(
            name="Repository",
            rootReference="approved",
            defaultBranch="main",
            allowedBaseBranches=["main"],
        ),
    )
    task = await services.create_code_task(
        project.id,
        CodeTaskCreate(
            repositoryId=repository.id,
            changeContract=ChangeContract(
                objective="Block forbidden path",
                acceptanceCriteria=["Diff is withheld"],
                allowedPaths=["safe.py"],
                maxChangedFiles=1,
                maxDiffLines=10,
            ),
        ),
    )
    (Path(task.worktree_path) / "outside.py").write_text("SECRET = 'withheld'\n")
    app = FastAPI()
    app.include_router(build_platform_router(services))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        response = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/diff"
        )
    diff = response.json()["data"]["diff"]
    assert diff["diff"] == ""
    assert diff["scopeStatus"].startswith("blocked:")
    assert "SECRET" not in response.text
