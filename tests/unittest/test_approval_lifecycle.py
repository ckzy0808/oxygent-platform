"""Approval state machine and isolated Git lifecycle tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from oxygent.platform import (
    ApplyChangesRequest,
    ApprovalAction,
    ApprovalActionRequest,
    ApprovalActorType,
    ApprovalState,
    ChangeContract,
    CodeTaskCreate,
    DiscardChangesRequest,
    PlatformServices,
    ProjectCreate,
    ProjectTaskRisk,
    RepositoryRegistration,
    ScopeViolation,
    VerificationCommand,
    VerificationProfileCreate,
)


def git(root: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=check, capture_output=True, text=True
    ).stdout.strip()


async def approval_workspace(
    tmp_path: Path,
    *,
    risk: ProjectTaskRisk = ProjectTaskRisk.MEDIUM,
    verify: bool = True,
):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "OxyGent Test")
    (source / "src").mkdir()
    (source / "src" / "app.py").write_text("VALUE = 1\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    services = PlatformServices.with_code_workspace(
        repository_roots={"approved": source},
        workspace_root=tmp_path / "worktrees",
        verification_executables={sys.executable},
    )
    project = await services.create_project(ProjectCreate(name="Approval Project"))
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
        id="unit",
        name="Unit",
        slot="unit",
        argv=[sys.executable, "-c", "print('verified')"],
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
                objective="Approve safely",
                acceptanceCriteria=["Original repository is unchanged"],
                allowedPaths=["src/**"],
                maxChangedFiles=5,
                maxDiffLines=100,
                verificationProfileId=profile.id,
                risk=risk,
            ),
        ),
    )
    worktree = Path(task.worktree_path)
    (worktree / "src" / "app.py").write_text("VALUE = 2\n")
    run = None
    if verify:
        run = await services.run_verification(
            project.id, task.id, profile.id, command.id
        )
    return services, project, repository, task, source, worktree, run


def human(reason: str = "Reviewed") -> ApprovalActionRequest:
    return ApprovalActionRequest(
        actorId="reviewer@example.invalid",
        actorType=ApprovalActorType.HUMAN,
        reason=reason,
    )


@pytest.mark.asyncio
async def test_approve_is_audit_only_and_does_not_mutate_git(tmp_path: Path):
    services, project, _, task, source, worktree, run = await approval_workspace(
        tmp_path
    )
    head_before = git(worktree, "rev-parse", "HEAD")
    status_before = git(worktree, "status", "--short")

    updated, record = await services.approve_code_changes(project.id, task.id, human())

    assert updated.approval_state is ApprovalState.APPROVED
    assert record.action is ApprovalAction.APPROVE_CHANGES
    assert record.content_hash == run.content_hash
    assert run.id in record.verification_run_ids
    assert git(worktree, "rev-parse", "HEAD") == head_before
    assert git(worktree, "status", "--short") == status_before
    assert git(source, "status", "--short") == ""
    with pytest.raises(ValidationError, match="frozen"):
        record.reason = "mutated"


@pytest.mark.asyncio
async def test_apply_rejects_stale_approval_and_stale_verification(tmp_path: Path):
    services, project, _, task, _, worktree, _ = await approval_workspace(tmp_path)
    await services.approve_code_changes(project.id, task.id, human())
    (worktree / "src" / "app.py").write_text("VALUE = 3\n")

    with pytest.raises(ScopeViolation, match="approved content is stale"):
        await services.apply_code_changes(
            project.id,
            task.id,
            ApplyChangesRequest(
                actorId="local-user",
                commitMessage="Apply approved changes",
            ),
        )
    assert git(worktree, "rev-parse", "HEAD") == task.base_commit


@pytest.mark.asyncio
async def test_apply_commits_only_to_isolated_task_branch(tmp_path: Path):
    services, project, _, task, source, worktree, _ = await approval_workspace(tmp_path)
    await services.approve_code_changes(project.id, task.id, human())

    updated, record = await services.apply_code_changes(
        project.id,
        task.id,
        ApplyChangesRequest(
            actorId="local-user",
            actorType="human",
            reason="Explicit apply",
            commitMessage="Apply approved changes",
        ),
    )

    assert updated.approval_state is ApprovalState.APPLIED
    assert updated.applied_commit != task.base_commit
    assert record.applied_commit == updated.applied_commit
    assert git(worktree, "status", "--short") == ""
    assert git(source, "rev-parse", "main") == task.base_commit
    assert git(source, "rev-parse", task.branch) == updated.applied_commit
    assert git(source, "status", "--short") == ""


@pytest.mark.asyncio
async def test_apply_requires_fresh_successful_verification(tmp_path: Path):
    services, project, _, task, _, worktree, _ = await approval_workspace(
        tmp_path, verify=False
    )
    await services.approve_code_changes(project.id, task.id, human())

    with pytest.raises(ScopeViolation, match="verification is missing or stale"):
        await services.apply_code_changes(
            project.id,
            task.id,
            ApplyChangesRequest(actorId="local-user", commitMessage="Must not apply"),
        )
    assert git(worktree, "rev-parse", "HEAD") == task.base_commit


@pytest.mark.asyncio
async def test_high_risk_approval_requires_human_actor(tmp_path: Path):
    services, project, _, task, _, _, _ = await approval_workspace(
        tmp_path, risk=ProjectTaskRisk.HIGH
    )
    with pytest.raises(ScopeViolation, match="human approval"):
        await services.approve_code_changes(
            project.id,
            task.id,
            ApprovalActionRequest(
                actorId="review-agent",
                actorType=ApprovalActorType.AGENT,
            ),
        )
    updated, _ = await services.approve_code_changes(project.id, task.id, human())
    assert updated.approval_state is ApprovalState.APPROVED


@pytest.mark.asyncio
async def test_request_revision_clears_approval_hash(tmp_path: Path):
    services, project, _, task, _, _, _ = await approval_workspace(tmp_path)
    await services.approve_code_changes(project.id, task.id, human())

    updated, record = await services.request_code_revision(
        project.id, task.id, human("Please add a regression test")
    )

    assert updated.approval_state is ApprovalState.REVISION_REQUESTED
    assert updated.approved_content_hash is None
    assert record.action is ApprovalAction.REQUEST_REVISION


@pytest.mark.asyncio
async def test_export_and_discard_preserve_recovery_patch_before_cleanup(
    tmp_path: Path,
):
    services, project, _, task, source, worktree, _ = await approval_workspace(tmp_path)
    patch, export_record = await services.export_code_patch(
        project.id, task.id, human("Export before decision")
    )
    assert "+VALUE = 2" in patch.content
    assert export_record.recovery_patch_id == patch.id

    updated, discard_record, recovery = await services.discard_code_task(
        project.id,
        task.id,
        DiscardChangesRequest(
            actorId="local-user",
            actorType="human",
            reason="No longer needed",
            confirmation="DISCARD",
        ),
    )

    assert updated.approval_state is ApprovalState.DISCARDED
    assert updated.recovery_patch_id == recovery.id
    assert discard_record.recovery_patch_id == recovery.id
    assert not worktree.exists()
    assert git(source, "rev-parse", "--verify", task.branch, check=False) == ""
    stored = await services.get_recovery_patch(project.id, task.id, recovery.id)
    assert "+VALUE = 2" in stored.content
    assert git(source, "status", "--short") == ""


def test_discard_requires_explicit_confirmation():
    with pytest.raises(ValidationError, match="exactly equal DISCARD"):
        DiscardChangesRequest(actorId="local-user", confirmation="yes")
