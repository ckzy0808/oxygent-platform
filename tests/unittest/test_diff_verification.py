"""Bounded diff and fixed-argv verification tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from oxygent.platform import (
    ChangeContract,
    CodeTaskCreate,
    PlatformServices,
    ProjectCreate,
    RepositoryRegistration,
    VerificationCommand,
    VerificationProfileCreate,
    VerificationSlot,
    VerificationStatus,
)
from oxygent.platform.verification import (
    VerificationFailureCategory,
    classify_verification_failure,
    sanitize_verification_output,
    verification_output_preview,
)


def test_verification_failure_classifier_separates_dependency_network_errors():
    category = classify_verification_failure(
        status=VerificationStatus.FAILED,
        stderr=(
            "Could not transfer artifact org.ow2.asm:asm:jar:9.8: "
            "Premature end of Content-Length delimited message body"
        ),
    )

    assert category is VerificationFailureCategory.INFRASTRUCTURE


def test_verification_failure_classifier_keeps_test_assertions_actionable():
    category = classify_verification_failure(
        status=VerificationStatus.FAILED,
        stdout="AssertionError: expected 2 but got 1",
    )

    assert category is VerificationFailureCategory.CODE


def test_auto_maven_command_uses_bounded_transport_retry(monkeypatch, tmp_path):
    from oxygent.platform.services import PlatformServices

    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    monkeypatch.setattr(
        "oxygent.platform.services._verification_executable",
        lambda name: "/opt/toolchain/bin/mvn" if name == "mvn" else None,
    )

    command = next(
        item
        for item in PlatformServices._code_stage_verification_commands(tmp_path)
        if item.id.startswith("maven-build-")
    )

    assert "--batch-mode" in command.argv
    assert "--no-transfer-progress" in command.argv
    assert "-Dmaven.wagon.http.retryHandler.count=3" in command.argv


def test_verification_output_removes_ansi_and_keeps_error_tail():
    cleaned = sanitize_verification_output(
        "\x1b[2K\rtransforming...\x1b[31mfailed\x1b[0m\n"
    )
    preview = verification_output_preview("start\n" + "x" * 100 + "\nERROR line 23", 60)

    assert "\x1b" not in cleaned
    assert "transforming...failed" in cleaned.replace("\n", "")
    assert preview.startswith("start")
    assert preview.endswith("ERROR line 23")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


async def workspace(tmp_path: Path, *, max_files: int = 20):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-b", "main")
    git(source, "config", "user.email", "test@example.invalid")
    git(source, "config", "user.name", "OxyGent Test")
    (source / "src").mkdir()
    (source / "src" / "main.py").write_text("VALUE = 1\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "initial")
    services = PlatformServices.with_code_workspace(
        repository_roots={"approved": source},
        workspace_root=tmp_path / "worktrees",
        verification_executables={sys.executable},
    )
    project = await services.create_project(ProjectCreate(name="Verification Project"))
    repository = await services.register_repository(
        project.id,
        RepositoryRegistration(
            name="Repository",
            rootReference="approved",
            defaultBranch="main",
            allowedBaseBranches=["main"],
        ),
    )
    profile = await services.register_verification_profile(
        project.id,
        VerificationProfileCreate(
            repositoryId=repository.id,
            name="Checks",
            commands=[
                VerificationCommand(
                    id="unit",
                    name="Unit",
                    slot=VerificationSlot.UNIT,
                    argv=[sys.executable, "-c", "print('passed')"],
                )
            ],
        ),
    )
    task = await services.create_code_task(
        project.id,
        CodeTaskCreate(
            repositoryId=repository.id,
            changeContract=ChangeContract(
                objective="Verify changes",
                acceptanceCriteria=["Checks use real exit codes"],
                allowedPaths=["src/**"],
                maxChangedFiles=max_files,
                maxDiffLines=100,
                verificationProfileId=profile.id,
            ),
        ),
    )
    return services, project, task, profile, source


@pytest.mark.asyncio
async def test_diff_includes_tracked_and_untracked_files(tmp_path: Path):
    services, project, task, _, source = await workspace(tmp_path)
    worktree = Path(task.worktree_path)
    (worktree / "src" / "main.py").write_text("VALUE = 2\n")
    (worktree / "src" / "new.py").write_text("NEW = True\n")

    snapshot = await services.get_code_diff(project.id, task.id)

    assert snapshot.changed_files == ["src/main.py", "src/new.py"]
    assert "-VALUE = 1" in snapshot.diff
    assert "+VALUE = 2" in snapshot.diff
    assert "+NEW = True" in snapshot.diff
    assert snapshot.additions == 2
    assert snapshot.deletions == 1
    assert snapshot.scope_status == "valid"
    assert git(source, "status", "--short") == ""


def test_verification_command_rejects_shell_interpreters():
    with pytest.raises(ValidationError, match="shell interpreters"):
        VerificationCommand(
            name="Unsafe",
            slot="unit",
            argv=["sh", "-c", "echo unsafe"],
        )


@pytest.mark.asyncio
async def test_shell_metacharacters_remain_literal_arguments(tmp_path: Path):
    services, project, task, profile, _ = await workspace(tmp_path)
    literal = "; touch should-not-exist"
    command = VerificationCommand(
        id="literal",
        name="Literal argument",
        slot="unit",
        argv=[sys.executable, "-c", "import sys; print(sys.argv[1])", literal],
    )
    profile = profile.model_copy(update={"commands": [command]})
    await services.verification_profiles.update(profile)

    run = await services.run_verification(project.id, task.id, profile.id, command.id)

    assert run.status is VerificationStatus.PASSED
    assert literal in run.stdout_preview
    assert not (Path(task.worktree_path) / "should-not-exist").exists()


@pytest.mark.asyncio
async def test_verification_records_real_nonzero_exit_and_output_artifacts(
    tmp_path: Path,
):
    services, project, task, profile, _ = await workspace(tmp_path)
    command = VerificationCommand(
        id="failure",
        name="Failing check",
        slot="lint",
        argv=[
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
        ],
    )
    profile = profile.model_copy(update={"commands": [command]})
    await services.verification_profiles.update(profile)

    run = await services.run_verification(project.id, task.id, profile.id, command.id)
    stdout = await services.get_verification_output(
        project.id, task.id, run.stdout_artifact_id
    )
    stderr = await services.get_verification_output(
        project.id, task.id, run.stderr_artifact_id
    )

    assert run.status is VerificationStatus.FAILED
    assert run.exit_code == 7
    assert stdout.content.strip() == "out"
    assert stderr.content.strip() == "err"
    assert len(run.command_definition_hash) == 64


@pytest.mark.asyncio
async def test_verification_uses_validated_relative_working_directory(tmp_path: Path):
    services, project, task, profile, _ = await workspace(tmp_path)
    command = VerificationCommand(
        id="cwd",
        name="Working directory",
        slot="compile",
        argv=[sys.executable, "-c", "from pathlib import Path; print(Path.cwd().name)"],
        workingDirectory="src",
    )
    profile = profile.model_copy(update={"commands": [command]})
    await services.verification_profiles.update(profile)

    run = await services.run_verification(project.id, task.id, profile.id, command.id)

    assert run.status is VerificationStatus.PASSED
    assert run.stdout_preview.strip() == "src"


@pytest.mark.asyncio
async def test_timeout_and_output_limit_are_reported(tmp_path: Path):
    services, project, task, profile, _ = await workspace(tmp_path)
    timeout = VerificationCommand(
        id="timeout",
        name="Timeout",
        slot="unit",
        argv=[sys.executable, "-c", "import time; time.sleep(3)"],
        timeoutSeconds=1,
    )
    profile_timeout = profile.model_copy(update={"commands": [timeout]})
    await services.verification_profiles.update(profile_timeout)
    timed_out = await services.run_verification(
        project.id, task.id, profile.id, timeout.id
    )
    assert timed_out.status is VerificationStatus.TIMED_OUT

    output = VerificationCommand(
        id="output",
        name="Bounded output",
        slot="unit",
        argv=[sys.executable, "-c", "print('x' * 12000)"],
        maxOutputBytes=4096,
    )
    profile_output = profile.model_copy(update={"commands": [output]})
    await services.verification_profiles.update(profile_output)
    limited = await services.run_verification(
        project.id, task.id, profile.id, output.id
    )
    assert limited.status is VerificationStatus.PASSED
    assert limited.output_truncated is True
    artifact = await services.get_verification_output(
        project.id, task.id, limited.stdout_artifact_id
    )
    assert len(artifact.content.encode()) == 4096
    assert artifact.truncated is True


@pytest.mark.asyncio
async def test_scope_guard_blocks_command_before_execution(tmp_path: Path):
    services, project, task, profile, _ = await workspace(tmp_path, max_files=1)
    worktree = Path(task.worktree_path)
    (worktree / "src" / "a.py").write_text("A = 1\n")
    (worktree / "src" / "b.py").write_text("B = 1\n")
    marker = worktree / "marker"
    command = VerificationCommand(
        id="must-not-run",
        name="Must not run",
        slot="unit",
        argv=[
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        ],
    )
    profile = profile.model_copy(update={"commands": [command]})
    await services.verification_profiles.update(profile)

    run = await services.run_verification(project.id, task.id, profile.id, command.id)

    assert run.status is VerificationStatus.BLOCKED
    assert "changed file limit" in run.failure_reason
    assert run.exit_code is None
    assert not marker.exists()
