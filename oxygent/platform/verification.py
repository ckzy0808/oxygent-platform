"""Bounded diff capture and fixed-argv verification for Code Workspace."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import time
from collections.abc import Iterable, Mapping
from datetime import datetime
from enum import Enum
from pathlib import Path

from pydantic import Field, field_validator

from oxygent.utils.common_utils import generate_uuid

from .coding import (
    ChangeContract,
    CodeWorkspaceError,
    ScopeGuard,
    ScopeViolation,
    normalize_relative_path,
    run_git,
)
from .common import PlatformModel, utc_now


class VerificationSlot(str, Enum):
    FORMAT = "format"
    LINT = "lint"
    TYPECHECK = "typecheck"
    COMPILE = "compile"
    UNIT = "unit"
    INTEGRATION = "integration"
    BUILD = "build"


class VerificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMED_OUT = "timedOut"


class VerificationFailureCategory(str, Enum):
    CODE = "code"
    INFRASTRUCTURE = "infrastructure"


_INFRASTRUCTURE_FAILURE_PATTERNS = (
    "could not transfer artifact",
    "could not resolve dependencies",
    "failed to collect dependencies",
    "premature end of content-length",
    "connection reset",
    "connection refused",
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "could not resolve host",
    "econnreset",
    "econnrefused",
    "enotfound",
    "eai_again",
    "network timeout",
    "unable to access",
    "command not found",
    "no such file or directory",
)
_ANSI_CONTROL_SEQUENCE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def sanitize_verification_output(value: str) -> str:
    """Remove terminal rendering codes while preserving actionable build text."""

    value = _ANSI_CONTROL_SEQUENCE.sub("", value).replace("\r", "\n")
    return "".join(
        character
        for character in value
        if character in {"\n", "\t"} or ord(character) >= 32
    )


def verification_output_preview(value: str, limit: int) -> str:
    """Keep command startup context and the error-heavy tail of long output."""

    if len(value) <= limit:
        return value
    head_size = min(8_000, limit // 4)
    marker = "\n... 中间输出已省略，保留末尾错误信息 ...\n"
    tail_size = max(0, limit - head_size - len(marker))
    return value[:head_size] + marker + value[-tail_size:]


def classify_verification_failure(
    *,
    status: VerificationStatus,
    stdout: str = "",
    stderr: str = "",
    failure_reason: str = "",
) -> VerificationFailureCategory | None:
    """Separate host/dependency failures from source-code failures."""

    if status is VerificationStatus.PASSED:
        return None
    text = "\n".join((stdout, stderr, failure_reason)).lower()
    if any(pattern in text for pattern in _INFRASTRUCTURE_FAILURE_PATTERNS):
        return VerificationFailureCategory.INFRASTRUCTURE
    return VerificationFailureCategory.CODE


_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,79}$")
_SHELL_EXECUTABLES = {
    "bash",
    "cmd",
    "cmd.exe",
    "csh",
    "dash",
    "fish",
    "ksh",
    "powershell",
    "pwsh",
    "sh",
    "tcsh",
    "zsh",
}


class VerificationCommand(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    name: str = Field(min_length=1, max_length=120)
    slot: VerificationSlot
    argv: list[str] = Field(min_length=1, max_length=100)
    timeout_seconds: float = Field(default=120.0, ge=1.0, le=3600.0)
    working_directory: str = Field(default=".", max_length=500)
    allowed_environment: list[str] = Field(default_factory=list, max_length=50)
    max_output_bytes: int = Field(default=1_000_000, ge=4096, le=10_000_000)

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 2000 or "\x00" in value for value in values):
            raise ValueError("argv entries must be non-empty and contain no NUL bytes")
        executable = Path(values[0]).name.lower()
        if executable in _SHELL_EXECUTABLES:
            raise ValueError(
                "shell interpreters are not valid verification executables"
            )
        return values

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str) -> str:
        return normalize_relative_path(value, allow_root=True)

    @field_validator("allowed_environment")
    @classmethod
    def validate_environment(cls, values: list[str]) -> list[str]:
        if any(not _ENV_NAME.fullmatch(value) for value in values):
            raise ValueError("allowed environment names must be uppercase identifiers")
        return list(dict.fromkeys(values))


class VerificationProfile(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    commands: list[VerificationCommand] = Field(min_length=1, max_length=30)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class VerificationProfileCreate(PlatformModel):
    repository_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    commands: list[VerificationCommand] = Field(min_length=1, max_length=30)


class DiffSnapshot(PlatformModel):
    base_commit: str
    changed_files: list[str] = Field(default_factory=list)
    diff: str = ""
    diff_line_count: int = Field(default=0, ge=0)
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    truncated: bool = False
    scope_status: str = "valid"


class VerificationOutput(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str
    task_id: str
    stream: str
    content: str
    truncated: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class VerificationRun(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str
    task_id: str
    profile_id: str
    command_id: str
    command_name: str
    slot: VerificationSlot
    argv: list[str]
    working_directory: str
    command_definition_hash: str
    status: VerificationStatus
    exit_code: int | None = None
    duration_ms: float = Field(ge=0)
    stdout_preview: str = ""
    stderr_preview: str = ""
    stdout_artifact_id: str | None = None
    stderr_artifact_id: str | None = None
    output_truncated: bool = False
    failure_reason: str | None = None
    failure_category: VerificationFailureCategory | None = None
    attempt_count: int = Field(default=1, ge=1, le=10)
    automatic_retry_reason: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    diff_line_count: int = Field(default=0, ge=0)
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=utc_now)


class InMemoryVerificationProfileStore:
    def __init__(self, profiles: Iterable[VerificationProfile] | None = None) -> None:
        self._profiles = {item.id: item for item in profiles or []}
        self._lock = asyncio.Lock()

    async def create(self, profile: VerificationProfile) -> VerificationProfile:
        async with self._lock:
            if profile.id in self._profiles:
                raise ValueError(f"verification profile already exists: {profile.id}")
            self._profiles[profile.id] = profile
        return profile

    async def get(self, profile_id: str) -> VerificationProfile:
        async with self._lock:
            try:
                return self._profiles[profile_id]
            except KeyError as exc:
                raise KeyError(f"verification profile not found: {profile_id}") from exc

    async def list(self, project_id: str | None = None) -> list[VerificationProfile]:
        async with self._lock:
            values = list(self._profiles.values())
        if project_id is not None:
            values = [item for item in values if item.project_id == project_id]
        return sorted(values, key=lambda item: item.created_at)

    async def update(self, profile: VerificationProfile) -> VerificationProfile:
        async with self._lock:
            if profile.id not in self._profiles:
                raise KeyError(f"verification profile not found: {profile.id}")
            self._profiles[profile.id] = profile
        return profile


class InMemoryVerificationRunStore:
    def __init__(self) -> None:
        self._runs: list[VerificationRun] = []
        self._outputs: dict[str, VerificationOutput] = {}
        self._lock = asyncio.Lock()

    async def append(
        self,
        run: VerificationRun,
        outputs: Iterable[VerificationOutput] = (),
    ) -> VerificationRun:
        async with self._lock:
            self._runs.append(run)
            for output in outputs:
                self._outputs[output.id] = output
        return run

    async def list(self, task_id: str | None = None) -> list[VerificationRun]:
        async with self._lock:
            values = list(self._runs)
        if task_id is not None:
            values = [item for item in values if item.task_id == task_id]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    async def get_output(self, output_id: str) -> VerificationOutput:
        async with self._lock:
            try:
                return self._outputs[output_id]
            except KeyError as exc:
                raise KeyError(f"verification output not found: {output_id}") from exc


async def capture_diff(
    worktree: Path,
    base_commit: str,
    *,
    output_limit: int = 1_000_000,
) -> DiffSnapshot:
    """Capture tracked and untracked changes as a bounded unified diff."""
    worktree = worktree.resolve(strict=True)
    _, names, _, names_truncated = await run_git(
        worktree,
        "diff",
        "--name-only",
        "--no-renames",
        base_commit,
        "--",
        output_limit=output_limit,
    )
    _, untracked, _, untracked_truncated = await run_git(
        worktree,
        "ls-files",
        "--others",
        "--exclude-standard",
        output_limit=output_limit,
    )
    changed_files = list(
        dict.fromkeys(
            normalize_relative_path(item)
            for item in [*names.splitlines(), *untracked.splitlines()]
            if item
        )
    )
    _, tracked_diff, _, tracked_truncated = await run_git(
        worktree,
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--no-renames",
        "--unified=3",
        base_commit,
        "--",
        output_limit=output_limit,
    )
    chunks = [tracked_diff]
    truncated = names_truncated or untracked_truncated or tracked_truncated
    remaining = max(0, output_limit - len(tracked_diff.encode("utf-8")))
    for path in untracked.splitlines():
        if remaining <= 0:
            truncated = True
            break
        _, addition, _, addition_truncated = await run_git(
            worktree,
            "diff",
            "--no-ext-diff",
            "--no-color",
            "--no-index",
            "--unified=3",
            "--",
            os.devnull,
            path,
            output_limit=remaining,
            allowed_exit_codes={0, 1},
        )
        chunks.append(addition)
        remaining -= len(addition.encode("utf-8"))
        truncated = truncated or addition_truncated
    diff = "".join(chunks)
    additions = sum(
        1
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    deletions = sum(
        1
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    return DiffSnapshot(
        baseCommit=base_commit,
        changedFiles=changed_files,
        diff=diff,
        diffLineCount=additions + deletions,
        additions=additions,
        deletions=deletions,
        truncated=truncated,
    )


class VerificationRunner:
    """Run approved executables with fixed argv, bounded output, and no shell."""

    def __init__(
        self,
        *,
        allowed_executables: Iterable[str] = (),
        environment: Mapping[str, str] | None = None,
        preview_bytes: int = 32_000,
    ) -> None:
        self.allowed_executables = {str(Path(item)) for item in allowed_executables}
        self.environment = dict(environment or {})
        self.preview_bytes = preview_bytes

    async def run(
        self,
        *,
        project_id: str,
        task_id: str,
        profile_id: str,
        command: VerificationCommand,
        worktree: Path,
        contract: ChangeContract,
        diff: DiffSnapshot,
    ) -> tuple[VerificationRun, list[VerificationOutput]]:
        started = time.monotonic()
        command_hash = _command_hash(command)
        content_hash = diff_content_hash(diff)
        try:
            ScopeGuard.check_diff(contract, diff.changed_files, diff.diff_line_count)
        except ScopeViolation as exc:
            return (
                VerificationRun(
                    projectId=project_id,
                    taskId=task_id,
                    profileId=profile_id,
                    commandId=command.id,
                    commandName=command.name,
                    slot=command.slot,
                    argv=command.argv,
                    workingDirectory=command.working_directory,
                    commandDefinitionHash=command_hash,
                    status=VerificationStatus.BLOCKED,
                    durationMs=(time.monotonic() - started) * 1000,
                    failureReason=str(exc),
                    changedFiles=diff.changed_files,
                    diffLineCount=diff.diff_line_count,
                    contentHash=content_hash,
                ),
                [],
            )
        executable = command.argv[0]
        if executable not in self.allowed_executables:
            raise CodeWorkspaceError(
                "verification executable is not approved by the host"
            )
        cwd = (worktree / command.working_directory).resolve(strict=True)
        try:
            cwd.relative_to(worktree)
        except ValueError as exc:
            raise ScopeViolation("verification cwd escapes the task worktree") from exc
        if not cwd.is_dir():
            raise CodeWorkspaceError(
                "verification working directory is not a directory"
            )
        environment = {
            key: value
            for key, value in self.environment.items()
            if key in {*command.allowed_environment, "PATH", "LANG", "LC_ALL"}
        }
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            cwd=str(cwd),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout_task = asyncio.create_task(
            _read_bounded(process.stdout, command.max_output_bytes)
        )
        stderr_task = asyncio.create_task(
            _read_bounded(process.stderr, command.max_output_bytes)
        )
        timed_out = False
        wait_task = asyncio.create_task(process.wait())
        try:
            await asyncio.wait_for(
                asyncio.shield(wait_task), timeout=command.timeout_seconds
            )
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await wait_task
        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task
        duration_ms = (time.monotonic() - started) * 1000
        stdout_output = VerificationOutput(
            projectId=project_id,
            taskId=task_id,
            stream="stdout",
            content=sanitize_verification_output(
                stdout.decode("utf-8", errors="replace")
            ),
            truncated=stdout_truncated,
        )
        stderr_output = VerificationOutput(
            projectId=project_id,
            taskId=task_id,
            stream="stderr",
            content=sanitize_verification_output(
                stderr.decode("utf-8", errors="replace")
            ),
            truncated=stderr_truncated,
        )
        status = (
            VerificationStatus.TIMED_OUT
            if timed_out
            else VerificationStatus.PASSED
            if process.returncode == 0
            else VerificationStatus.FAILED
        )
        run = VerificationRun(
            projectId=project_id,
            taskId=task_id,
            profileId=profile_id,
            commandId=command.id,
            commandName=command.name,
            slot=command.slot,
            argv=command.argv,
            workingDirectory=command.working_directory,
            commandDefinitionHash=command_hash,
            status=status,
            exitCode=process.returncode,
            durationMs=duration_ms,
            stdoutPreview=verification_output_preview(
                stdout_output.content, self.preview_bytes
            ),
            stderrPreview=verification_output_preview(
                stderr_output.content, self.preview_bytes
            ),
            stdoutArtifactId=stdout_output.id,
            stderrArtifactId=stderr_output.id,
            outputTruncated=stdout_truncated or stderr_truncated,
            failureReason=(
                "Verification command timed out"
                if timed_out
                else "Verification command exited non-zero"
                if process.returncode
                else None
            ),
            failureCategory=classify_verification_failure(
                status=status,
                stdout=stdout_output.content,
                stderr=stderr_output.content,
                failure_reason=(
                    "Verification command timed out"
                    if timed_out
                    else "Verification command exited non-zero"
                    if process.returncode
                    else ""
                ),
            ),
            changedFiles=diff.changed_files,
            diffLineCount=diff.diff_line_count,
            contentHash=content_hash,
        )
        return run, [stdout_output, stderr_output]


async def _read_bounded(
    stream: asyncio.StreamReader | None, limit: int
) -> tuple[bytes, bool]:
    if stream is None:
        return b"", False
    chunks: list[bytes] = []
    size = 0
    truncated = False
    while chunk := await stream.read(65_536):
        remaining = max(0, limit - size)
        if remaining:
            chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
        if len(chunk) > remaining:
            truncated = True
    return b"".join(chunks), truncated


def _command_hash(command: VerificationCommand) -> str:
    encoded = json.dumps(
        command.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def diff_content_hash(snapshot: DiffSnapshot) -> str:
    """Bind approvals and verification to the exact base and diff bytes."""
    encoded = json.dumps(
        {
            "baseCommit": snapshot.base_commit,
            "changedFiles": snapshot.changed_files,
            "diff": snapshot.diff,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
