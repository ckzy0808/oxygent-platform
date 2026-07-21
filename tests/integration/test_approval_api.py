"""Approval API separation, audit, and recovery patch tests."""

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
async def test_approval_and_apply_are_separate_http_actions(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "OxyGent Test")
    (source / "app.py").write_text("VALUE = 1\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    services = PlatformServices.with_code_workspace(
        repository_roots={"approved": source},
        workspace_root=tmp_path / "worktrees",
        verification_executables={sys.executable},
    )
    project = await services.create_project(ProjectCreate(name="Approval API"))
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
        id="check",
        name="Check",
        slot="unit",
        argv=[sys.executable, "-c", "print('ok')"],
    )
    profile = await services.register_verification_profile(
        project.id,
        VerificationProfileCreate(
            repositoryId=repository.id, name="Checks", commands=[command]
        ),
    )
    task = await services.create_code_task(
        project.id,
        CodeTaskCreate(
            repositoryId=repository.id,
            changeContract=ChangeContract(
                objective="Approval API",
                acceptanceCriteria=["Approval is not Apply"],
                allowedPaths=["app.py"],
                verificationProfileId=profile.id,
            ),
        ),
    )
    worktree = Path(task.worktree_path)
    (worktree / "app.py").write_text("VALUE = 2\n")
    await services.run_verification(project.id, task.id, profile.id, command.id)
    app = FastAPI()
    app.include_router(build_platform_router(services))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        approved = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/approve",
            json={"actorId": "human", "actorType": "human", "reason": "Reviewed"},
        )
        assert approved.status_code == 201
        assert approved.json()["data"]["task"]["approvalState"] == "approved"
        assert git(worktree, "rev-parse", "HEAD") == task.base_commit

        applied = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/apply",
            json={
                "actorId": "human",
                "actorType": "human",
                "reason": "Apply now",
                "commitMessage": "Apply approved changes",
            },
        )
        assert applied.status_code == 201
        applied_task = applied.json()["data"]["task"]
        assert applied_task["approvalState"] == "applied"
        assert applied_task["appliedCommit"] != task.base_commit
        assert git(source, "rev-parse", "main") == task.base_commit

        audit = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/approvals"
        )
        assert [item["action"] for item in audit.json()["data"]["items"]] == [
            "approveChanges",
            "applyToBranch",
        ]

        exported = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/export-patch",
            json={"actorId": "human", "actorType": "human", "reason": "Archive"},
        )
        assert "content" not in exported.json()["data"]["patch"]
        patch_id = exported.json()["data"]["patch"]["id"]
        patch = await client.get(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/recovery-patches/{patch_id}"
        )
        assert "+VALUE = 2" in patch.json()["data"]["patch"]["content"]


@pytest.mark.asyncio
async def test_discard_api_requires_confirmation_before_cleanup(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "OxyGent Test")
    (source / "app.py").write_text("VALUE = 1\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    services = PlatformServices.with_code_workspace(
        repository_roots={"approved": source},
        workspace_root=tmp_path / "worktrees",
    )
    project = await services.create_project(ProjectCreate(name="Discard API"))
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
                objective="Discard API",
                acceptanceCriteria=["Recovery first"],
                allowedPaths=["app.py"],
            ),
        ),
    )
    worktree = Path(task.worktree_path)
    (worktree / "app.py").write_text("VALUE = 2\n")
    app = FastAPI()
    app.include_router(build_platform_router(services))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        rejected = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/discard",
            json={"actorId": "human", "confirmation": "yes"},
        )
        assert rejected.status_code == 422
        assert worktree.exists()
        discarded = await client.post(
            f"/api/v1/platform/projects/{project.id}/code-tasks/{task.id}/discard",
            json={"actorId": "human", "confirmation": "DISCARD"},
        )
        assert discarded.status_code == 201
        data = discarded.json()["data"]
        assert data["task"]["approvalState"] == "discarded"
        assert data["recoveryPatch"]["id"]
        assert not worktree.exists()
