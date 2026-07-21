"""Safe repository and isolated worktree primitives for Code Workspace."""

from __future__ import annotations

import asyncio
import fnmatch
import re
from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from pydantic import Field, field_validator, model_validator

from oxygent.utils.common_utils import generate_uuid

from .common import PlatformModel, utc_now
from .projects import ProjectTaskRisk


class CodeWorkspaceError(RuntimeError):
    """Base error raised by the local Code Workspace boundary."""


class GitOperationError(CodeWorkspaceError):
    """Raised when a fixed-argument Git operation fails."""


class ScopeViolation(CodeWorkspaceError):
    """Raised when repository access or a diff violates its Change Contract."""


class CodeTaskStatus(str, Enum):
    PREPARING = "preparing"
    READY = "ready"
    BLOCKED = "blocked"
    FAILED = "failed"


class CodingOperation(str, Enum):
    METADATA = "metadata"
    TREE = "tree"
    SEARCH = "search"
    READ_FILE = "readFile"
    DIFF = "diff"


_INVALID_BRANCH = re.compile(r"[\x00-\x20~^:?*\\\[]")
_DEPENDENCY_FILES = {
    "cargo.lock",
    "cargo.toml",
    "composer.json",
    "composer.lock",
    "gemfile",
    "gemfile.lock",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
}
_SYSTEM_DENIED_PATTERNS = {
    ".env",
    ".env.*",
    ".git",
    ".git/**",
    "*.key",
    "*.pem",
}


def validate_git_branch(value: str) -> str:
    """Apply Git's important ref-name constraints before invoking Git."""
    if (
        not value
        or len(value) > 240
        or value.startswith(("-", ".", "/"))
        or value.endswith((".", "/", ".lock"))
        or ".." in value
        or "@{" in value
        or "//" in value
        or _INVALID_BRANCH.search(value)
    ):
        raise ValueError("invalid Git branch name")
    return value


def normalize_relative_path(value: str, *, allow_root: bool = False) -> str:
    """Normalize a browser/model supplied repository-relative POSIX path."""
    if "\\" in value:
        raise ScopeViolation("repository paths must use '/' separators")
    candidate = PurePosixPath(value or ".")
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ScopeViolation("repository paths must remain relative to the worktree")
    normalized = candidate.as_posix()
    if normalized in {"", "."}:
        if allow_root:
            return "."
        raise ScopeViolation("a repository-relative path is required")
    if normalized.startswith(".git/") or normalized == ".git":
        raise ScopeViolation("Git administration files are not readable")
    return normalized


class RepositorySource(PlatformModel):
    """Administrator-configured repository root exposed by opaque reference."""

    reference: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)


class RepositoryProfile(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    root_reference: str = Field(min_length=1, max_length=160)
    default_branch: str = Field(min_length=1, max_length=240)
    allowed_base_branches: list[str] = Field(min_length=1, max_length=50)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _validate_default_branch = field_validator("default_branch")(validate_git_branch)

    @field_validator("allowed_base_branches")
    @classmethod
    def validate_allowed_branches(cls, values: list[str]) -> list[str]:
        clean: list[str] = []
        for value in values:
            value = validate_git_branch(value)
            if value not in clean:
                clean.append(value)
        return clean

    @model_validator(mode="after")
    def default_must_be_allowed(self) -> RepositoryProfile:
        if self.default_branch not in self.allowed_base_branches:
            raise ValueError("default branch must be included in allowed base branches")
        return self


class RepositoryRegistration(PlatformModel):
    name: str = Field(min_length=1, max_length=160)
    root_reference: str = Field(min_length=1, max_length=160)
    default_branch: str = Field(min_length=1, max_length=240)
    allowed_base_branches: list[str] = Field(min_length=1, max_length=50)

    _validate_default_branch = field_validator("default_branch")(validate_git_branch)
    _validate_allowed_branches = field_validator("allowed_base_branches")(
        RepositoryProfile.validate_allowed_branches.__func__
    )


class ChangeContract(PlatformModel):
    objective: str = Field(min_length=1, max_length=4000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=100)
    allowed_paths: list[str] = Field(min_length=1, max_length=100)
    forbidden_paths: list[str] = Field(default_factory=list, max_length=100)
    max_changed_files: int = Field(default=20, ge=1, le=1000)
    max_diff_lines: int = Field(default=1000, ge=1, le=100000)
    dependency_changes_allowed: bool = False
    verification_profile_id: str | None = Field(default=None, max_length=160)
    risk: ProjectTaskRisk = ProjectTaskRisk.MEDIUM

    @field_validator("acceptance_criteria")
    @classmethod
    def nonempty_criteria(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 1000 for value in values):
            raise ValueError("acceptance criteria must be 1-1000 characters")
        return list(dict.fromkeys(value.strip() for value in values))

    @field_validator("allowed_paths", "forbidden_paths")
    @classmethod
    def safe_patterns(cls, values: list[str]) -> list[str]:
        clean: list[str] = []
        for value in values:
            if not value or len(value) > 300 or "\\" in value:
                raise ValueError("path patterns must be non-empty POSIX patterns")
            candidate = PurePosixPath(value)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("path patterns must remain repository-relative")
            if value not in clean:
                clean.append(value)
        return clean


class CodeTask(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str = Field(min_length=1)
    project_task_id: str | None = Field(default=None, max_length=160)
    repository_id: str = Field(min_length=1)
    base_branch: str = Field(min_length=1, max_length=240)
    base_commit: str = Field(min_length=7, max_length=64)
    branch: str = Field(min_length=1, max_length=240)
    worktree_path: str = Field(min_length=1, max_length=1000)
    change_contract: ChangeContract
    status: CodeTaskStatus = CodeTaskStatus.READY
    changed_files: list[str] = Field(default_factory=list)
    diff_line_count: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    _validate_base_branch = field_validator("base_branch")(validate_git_branch)
    _validate_task_branch = field_validator("branch")(validate_git_branch)


class CodeTaskCreate(PlatformModel):
    repository_id: str = Field(min_length=1)
    project_task_id: str | None = Field(default=None, max_length=160)
    base_branch: str | None = Field(default=None, max_length=240)
    change_contract: ChangeContract

    @field_validator("base_branch")
    @classmethod
    def validate_optional_branch(cls, value: str | None) -> str | None:
        return validate_git_branch(value) if value is not None else None


class CodingRunRequest(PlatformModel):
    task_id: str = Field(min_length=1)
    operation: CodingOperation
    worktree_path: str = Field(min_length=1)
    base_commit: str = Field(min_length=7, max_length=64)
    path: str | None = Field(default=None, max_length=1000)
    query: str | None = Field(default=None, max_length=500)
    max_results: int = Field(default=200, ge=1, le=2000)
    max_output_bytes: int = Field(default=256_000, ge=1024, le=2_000_000)


class CodingRunResult(PlatformModel):
    operation: CodingOperation
    data: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False


class CodingEngine(Protocol):
    async def execute(self, request: CodingRunRequest) -> CodingRunResult: ...


class InMemoryRepositoryProfileStore:
    def __init__(self, profiles: Iterable[RepositoryProfile] | None = None) -> None:
        self._profiles = {item.id: item for item in profiles or []}
        self._lock = asyncio.Lock()

    async def create(self, profile: RepositoryProfile) -> RepositoryProfile:
        async with self._lock:
            if profile.id in self._profiles:
                raise ValueError(f"repository already exists: {profile.id}")
            self._profiles[profile.id] = profile
        return profile

    async def get(self, profile_id: str) -> RepositoryProfile:
        async with self._lock:
            try:
                return self._profiles[profile_id]
            except KeyError as exc:
                raise KeyError(f"repository not found: {profile_id}") from exc

    async def list(self, project_id: str | None = None) -> list[RepositoryProfile]:
        async with self._lock:
            values = list(self._profiles.values())
        if project_id is not None:
            values = [item for item in values if item.project_id == project_id]
        return sorted(values, key=lambda item: item.created_at)


class InMemoryCodeTaskStore:
    def __init__(self, tasks: Iterable[CodeTask] | None = None) -> None:
        self._tasks = {item.id: item for item in tasks or []}
        self._lock = asyncio.Lock()

    async def create(self, task: CodeTask) -> CodeTask:
        async with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"code task already exists: {task.id}")
            self._tasks[task.id] = task
        return task

    async def get(self, task_id: str) -> CodeTask:
        async with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError as exc:
                raise KeyError(f"code task not found: {task_id}") from exc

    async def list(self, project_id: str | None = None) -> list[CodeTask]:
        async with self._lock:
            values = list(self._tasks.values())
        if project_id is not None:
            values = [item for item in values if item.project_id == project_id]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    async def update(self, task: CodeTask) -> CodeTask:
        async with self._lock:
            if task.id not in self._tasks:
                raise KeyError(f"code task not found: {task.id}")
            self._tasks[task.id] = task
        return task


class ScopeGuard:
    """System-enforced Change Contract checks, independent of model prompts."""

    @staticmethod
    def check_path(contract: ChangeContract, path: str) -> str:
        normalized = normalize_relative_path(path)
        if any(_path_matches(normalized, rule) for rule in _SYSTEM_DENIED_PATTERNS):
            raise ScopeViolation(f"path is denied by the platform: {normalized}")
        if not any(_path_matches(normalized, rule) for rule in contract.allowed_paths):
            raise ScopeViolation(f"path is outside allowed scope: {normalized}")
        if any(_path_matches(normalized, rule) for rule in contract.forbidden_paths):
            raise ScopeViolation(
                f"path is forbidden by the Change Contract: {normalized}"
            )
        if (
            not contract.dependency_changes_allowed
            and PurePosixPath(normalized).name.lower() in _DEPENDENCY_FILES
        ):
            raise ScopeViolation(
                "dependency changes are disabled by the Change Contract"
            )
        return normalized

    @classmethod
    def check_diff(
        cls,
        contract: ChangeContract,
        changed_files: Iterable[str],
        diff_line_count: int,
    ) -> list[str]:
        clean = list(
            dict.fromkeys(cls.check_path(contract, path) for path in changed_files)
        )
        if len(clean) > contract.max_changed_files:
            raise ScopeViolation(
                f"changed file limit exceeded: {len(clean)} > {contract.max_changed_files}"
            )
        if diff_line_count > contract.max_diff_lines:
            raise ScopeViolation(
                f"diff line limit exceeded: {diff_line_count} > {contract.max_diff_lines}"
            )
        return clean


def _path_matches(path: str, pattern: str) -> bool:
    if pattern in {"*", "**"}:
        return True
    normalized_pattern = pattern.rstrip("/")
    return (
        fnmatch.fnmatchcase(path, normalized_pattern)
        or fnmatch.fnmatchcase(path, f"{normalized_pattern}/**")
        or PurePosixPath(path).match(normalized_pattern)
    )


async def run_git(
    cwd: Path,
    *args: str,
    timeout: float = 30.0,
    output_limit: int = 2_000_000,
    allowed_exit_codes: set[int] | None = None,
) -> tuple[int, str, str, bool]:
    """Run Git without a shell and return bounded UTF-8 output."""
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise GitOperationError("Git operation timed out") from exc
    limit = max(1024, output_limit)
    truncated = len(stdout) > limit or len(stderr) > limit
    stdout_text = stdout[:limit].decode("utf-8", errors="replace")
    stderr_text = stderr[:limit].decode("utf-8", errors="replace")
    accepted = allowed_exit_codes or {0}
    if process.returncode not in accepted:
        message = stderr_text.strip() or stdout_text.strip() or "Git operation failed"
        raise GitOperationError(message[:1000])
    return process.returncode or 0, stdout_text, stderr_text, truncated


class NativeCodingEngine:
    """Read-only native engine. It cannot write repository files."""

    async def execute(self, request: CodingRunRequest) -> CodingRunResult:
        worktree = Path(request.worktree_path).resolve(strict=True)
        if not worktree.is_dir():
            raise CodeWorkspaceError("task worktree is unavailable")
        if request.operation is CodingOperation.METADATA:
            return await self._metadata(request, worktree)
        if request.operation is CodingOperation.TREE:
            return await self._tree(request, worktree)
        if request.operation is CodingOperation.SEARCH:
            return await self._search(request, worktree)
        if request.operation is CodingOperation.READ_FILE:
            return await self._read_file(request, worktree)
        if request.operation is CodingOperation.DIFF:
            return await self._diff(request, worktree)
        raise CodeWorkspaceError(f"unsupported coding operation: {request.operation}")

    async def _metadata(
        self, request: CodingRunRequest, worktree: Path
    ) -> CodingRunResult:
        _, head, _, truncated = await run_git(worktree, "rev-parse", "HEAD")
        _, branch, _, branch_truncated = await run_git(
            worktree, "branch", "--show-current"
        )
        _, status, _, status_truncated = await run_git(
            worktree,
            "status",
            "--short",
            "--untracked-files=all",
            output_limit=request.max_output_bytes,
        )
        return CodingRunResult(
            operation=request.operation,
            data={
                "head": head.strip(),
                "branch": branch.strip(),
                "clean": not bool(status.strip()),
                "status": status.splitlines()[: request.max_results],
            },
            truncated=truncated or branch_truncated or status_truncated,
        )

    async def _tree(self, request: CodingRunRequest, worktree: Path) -> CodingRunResult:
        root = normalize_relative_path(request.path or ".", allow_root=True)
        args = ["ls-files"]
        if root != ".":
            args.extend(["--", root])
        _, output, _, truncated = await run_git(
            worktree, *args, output_limit=request.max_output_bytes
        )
        files = output.splitlines()
        return CodingRunResult(
            operation=request.operation,
            data={"root": root, "files": files[: request.max_results]},
            truncated=truncated or len(files) > request.max_results,
        )

    async def _search(
        self, request: CodingRunRequest, worktree: Path
    ) -> CodingRunResult:
        if not request.query:
            raise CodeWorkspaceError("search query is required")
        root = normalize_relative_path(request.path or ".", allow_root=True)
        args = ["grep", "-n", "-I", "-F", "-e", request.query, "--"]
        if root != ".":
            args.extend(["--", root])
        _, output, _, truncated = await run_git(
            worktree,
            *args,
            output_limit=request.max_output_bytes,
            allowed_exit_codes={0, 1},
        )
        matches = output.splitlines()
        return CodingRunResult(
            operation=request.operation,
            data={"query": request.query, "matches": matches[: request.max_results]},
            truncated=truncated or len(matches) > request.max_results,
        )

    async def _read_file(
        self, request: CodingRunRequest, worktree: Path
    ) -> CodingRunResult:
        relative = normalize_relative_path(request.path or "")
        target = (worktree / relative).resolve(strict=True)
        try:
            target.relative_to(worktree)
        except ValueError as exc:
            raise ScopeViolation("file resolves outside the task worktree") from exc
        if not target.is_file():
            raise CodeWorkspaceError("repository path is not a file")
        content = await asyncio.to_thread(target.read_bytes)
        truncated = len(content) > request.max_output_bytes
        return CodingRunResult(
            operation=request.operation,
            data={
                "path": relative,
                "content": content[: request.max_output_bytes].decode(
                    "utf-8", errors="replace"
                ),
            },
            truncated=truncated,
        )

    async def _diff(self, request: CodingRunRequest, worktree: Path) -> CodingRunResult:
        _, output, _, truncated = await run_git(
            worktree,
            "diff",
            "--no-ext-diff",
            "--no-color",
            "--unified=3",
            request.base_commit,
            "--",
            output_limit=request.max_output_bytes,
        )
        return CodingRunResult(
            operation=request.operation,
            data={"baseCommit": request.base_commit, "diff": output},
            truncated=truncated,
        )


class WorktreeManager:
    """Creates isolated task worktrees from configured repository references."""

    def __init__(self, repository_roots: dict[str, Path], workspace_root: Path) -> None:
        self._repository_roots = {
            reference: path.expanduser().resolve()
            for reference, path in repository_roots.items()
        }
        self.workspace_root = workspace_root.expanduser().resolve()
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self._repository_roots)

    def sources(self) -> list[RepositorySource]:
        return [
            RepositorySource(reference=reference, name=path.name or reference)
            for reference, path in sorted(self._repository_roots.items())
        ]

    def resolve_repository(self, reference: str) -> Path:
        try:
            root = self._repository_roots[reference]
        except KeyError as exc:
            raise KeyError(f"repository source not allowed: {reference}") from exc
        if not root.is_dir() or not (root / ".git").exists():
            raise CodeWorkspaceError(
                "configured repository is not an available Git worktree"
            )
        return root

    async def inspect_repository(self, reference: str) -> dict[str, Any]:
        root = self.resolve_repository(reference)
        _, branch, _, _ = await run_git(root, "branch", "--show-current")
        _, commit, _, _ = await run_git(root, "rev-parse", "HEAD")
        _, remotes, _, _ = await run_git(root, "remote")
        return {
            "defaultBranch": branch.strip(),
            "head": commit.strip(),
            "remotes": remotes.splitlines(),
        }

    async def create_worktree(
        self,
        repository: RepositoryProfile,
        code_task_id: str,
        base_branch: str,
    ) -> tuple[str, str, Path]:
        if base_branch not in repository.allowed_base_branches:
            raise ScopeViolation("base branch is not allowed for this repository")
        root = self.resolve_repository(repository.root_reference)
        branch = _task_branch(code_task_id)
        target = (self.workspace_root / repository.id / code_task_id).resolve()
        try:
            target.relative_to(self.workspace_root)
        except ValueError as exc:
            raise ScopeViolation(
                "worktree target escapes the configured workspace root"
            ) from exc
        if target == root or root in target.parents:
            raise ScopeViolation(
                "task worktrees cannot be created inside the source repository"
            )
        async with self._lock:
            if target.exists():
                raise CodeWorkspaceError("task worktree already exists")
            _, commit, _, _ = await run_git(
                root, "rev-parse", "--verify", f"{base_branch}^{{commit}}"
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            await run_git(
                root,
                "worktree",
                "add",
                "-b",
                branch,
                str(target),
                commit.strip(),
                timeout=60.0,
            )
        return commit.strip(), branch, target


def _task_branch(task_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")[:48]
    branch = f"codex/code-{slug or 'task'}"
    return validate_git_branch(branch)
