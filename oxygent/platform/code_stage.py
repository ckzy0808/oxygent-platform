"""Simple project-source and Aider implementation-stage primitives.

Git remains an internal snapshot mechanism.  Product interfaces expose uploaded
project files, workflow artifacts, generated files, and downloadable results.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import zipfile
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Iterable

from pydantic import Field

from oxygent.utils.common_utils import generate_uuid

from .coding import CodeWorkspaceError, ScopeViolation, normalize_relative_path
from .common import PlatformModel, utc_now


IGNORED_SOURCE_PARTS = {
    ".conda-env",
    ".coverage",
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}

_AIDER_DENIED_PARTS = {".git", ".oxygent"}
_AIDER_DENIED_NAMES = {".env", ".env.local", ".env.production", ".gitignore"}
_IGNORED_SOURCE_NAMES = {".ds_store", "desktop.ini", "thumbs.db"}
_AIDER_MAX_EDITABLE_FILES = 24
_AIDER_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".go",
    ".h",
    ".html",
    ".java",
    ".js",
    ".jsx",
    ".json",
    ".kt",
    ".md",
    ".php",
    ".properties",
    ".py",
    ".rb",
    ".rs",
    ".scss",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
_AIDER_MANIFEST_NAMES = {
    "cargo.toml",
    "go.mod",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "requirements.txt",
}
_AIDER_PROTOCOL_MARKERS = (
    "<<<<<<< SEARCH",
    ">>>>>>> REPLACE",
    "<<<<<<< ORIGINAL",
    ">>>>>>> UPDATED",
    "*** Begin Patch",
    "*** End Patch",
)


def parse_model_json_object(output: str) -> dict[str, object]:
    """Extract the first valid JSON object from a model response."""
    decoder = json.JSONDecoder()
    position = output.find("{")
    while position >= 0:
        try:
            payload, _ = decoder.raw_decode(output[position:])
        except json.JSONDecodeError:
            position = output.find("{", position + 1)
            continue
        if isinstance(payload, dict):
            return payload
        position = output.find("{", position + 1)
    raise CodeWorkspaceError("model returned no valid JSON object")


def parse_aider_file_plan(
    output: str, *, limit: int = _AIDER_MAX_EDITABLE_FILES
) -> list[str]:
    """Parse and constrain a model-produced editable-file plan.

    The planner is untrusted: only repository-relative paths are accepted and
    internal, credential, and Aider files are never exposed as edit targets.
    """

    payload = parse_model_json_object(output)
    values = payload.get("files", [])
    if not isinstance(values, list):
        raise CodeWorkspaceError("code file planner returned an invalid file list")

    planned: list[str] = []
    for value in values:
        raw_path = value.get("path") if isinstance(value, dict) else value
        if not isinstance(raw_path, str):
            continue
        try:
            path = normalize_relative_path(raw_path.strip())
        except ScopeViolation:
            continue
        parts = PurePosixPath(path).parts
        name = parts[-1].lower()
        if (
            any(
                part in _AIDER_DENIED_PARTS or part.startswith(".aider")
                for part in parts
            )
            or name in _AIDER_DENIED_NAMES
            or name.endswith((".key", ".pem"))
        ):
            continue
        if path not in planned:
            planned.append(path)
        if len(planned) >= limit:
            break
    if not planned:
        raise CodeWorkspaceError("code file planner selected no safe project files")
    return planned


def extract_aider_suggested_paths(output: str) -> list[str]:
    """Recover safe file paths from Aider's add/approve-file response.

    Non-interactive Aider sometimes asks the caller to approve new files even
    when the implementation request is otherwise actionable. Terminal wrapping
    may split a suffix such as ``.java`` onto the next line, so line breaks
    inside Markdown code spans are normalized before applying the same safety
    constraints as the model file planner.
    """

    candidates: list[str] = []
    for match in re.finditer(r"`([^`]+)`", output, flags=re.DOTALL):
        raw_path = re.sub(r"\s*\r?\n\s*", "", match.group(1)).strip()
        if "/" not in raw_path and "\\" not in raw_path:
            continue
        try:
            planned = parse_aider_file_plan(json.dumps({"files": [raw_path]}))
        except CodeWorkspaceError:
            continue
        path = planned[0]
        if PurePosixPath(path).suffix.lower() not in _AIDER_SOURCE_SUFFIXES:
            continue
        if path not in candidates:
            candidates.append(path)
        if len(candidates) >= _AIDER_MAX_EDITABLE_FILES:
            break
    return candidates


def repository_file_list(root: Path, *, limit: int = 1200) -> list[str]:
    """Return a bounded, deterministic list of user project files."""

    files: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(
            part in _AIDER_DENIED_PARTS or part.startswith(".aider")
            for part in relative.parts
        ):
            continue
        files.append(relative.as_posix())
        if len(files) >= limit:
            break
    return files


def deterministic_aider_files(
    root: Path, *, limit: int = _AIDER_MAX_EDITABLE_FILES
) -> list[str]:
    """Select a small/medium repository's useful files without another LLM call.

    Lock files, generated output, credentials, and binary assets are intentionally
    omitted.  Returning an empty list tells the caller to use the model planner.
    """

    available = repository_file_list(root, limit=121)
    if not available or len(available) > 120:
        return []
    candidates = [
        path
        for path in available
        if (
            PurePosixPath(path).suffix.lower() in _AIDER_SOURCE_SUFFIXES
            or PurePosixPath(path).name.lower() in _AIDER_MANIFEST_NAMES
        )
        and not PurePosixPath(path)
        .name.lower()
        .endswith((".lock", "-lock.json", ".min.js", ".min.css"))
    ]
    if not candidates:
        return []

    # Keep manifests first, then interleave top-level areas so a mixed
    # backend/frontend repository does not lose one side to lexical sorting.
    selected: list[str] = []
    for path in candidates:
        if PurePosixPath(path).name.lower() in _AIDER_MANIFEST_NAMES:
            selected.append(path)
    groups: dict[str, list[str]] = {}
    for path in candidates:
        if path in selected:
            continue
        parts = PurePosixPath(path).parts
        groups.setdefault(parts[0] if len(parts) > 1 else "", []).append(path)
    while len(selected) < limit and groups:
        exhausted = []
        for group, paths in groups.items():
            if paths and len(selected) < limit:
                selected.append(paths.pop(0))
            if not paths:
                exhausted.append(group)
        for group in exhausted:
            groups.pop(group, None)
    return selected


def prepare_aider_editable_files(
    root: Path, paths: Iterable[str], *, create_missing: bool
) -> list[str]:
    """Resolve planned paths and optionally create blank-project placeholders."""

    editable: list[str] = []
    for path in paths:
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            continue
        if create_missing and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        if target.is_file():
            editable.append(path)
    if not editable:
        raise CodeWorkspaceError("no planned project files are available to Aider")
    return editable


def remove_empty_aider_placeholders(root: Path, paths: Iterable[str]) -> None:
    """Remove untouched placeholders created to authorize new Aider files."""

    for path in paths:
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            continue
        try:
            if target.stat().st_size == 0:
                target.unlink()
        except OSError:
            continue


def detect_aider_protocol_artifacts(root: Path, paths: Iterable[str]) -> list[str]:
    """Find source files polluted by unapplied Aider/patch protocol text."""

    polluted: list[str] = []
    for path in paths:
        relative = PurePosixPath(path)
        if relative.suffix.lower() not in _AIDER_SOURCE_SUFFIXES - {".md"}:
            continue
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            continue
        try:
            content = target.read_text(encoding="utf-8", errors="replace")[:2_000_000]
        except OSError:
            continue
        if any(marker in content for marker in _AIDER_PROTOCOL_MARKERS):
            polluted.append(path)
    return polluted


def expand_aider_retry_files(
    root: Path,
    selected: Iterable[str],
    *,
    preferred: Iterable[str] = (),
    request: str = "",
    limit: int = _AIDER_MAX_EDITABLE_FILES,
) -> list[str]:
    """Broaden a no-op Aider retry without exposing the whole repository."""

    available = repository_file_list(root, limit=3000)
    available_set = set(available)
    ordered: list[str] = []

    def add(path: str) -> None:
        if path in available_set and path not in ordered and len(ordered) < limit:
            ordered.append(path)

    for path in selected:
        add(path)
    for path in preferred:
        add(path)
    lowered_request = request.lower()
    for path in available:
        if (
            path.lower() in lowered_request
            or PurePosixPath(path).name.lower() in lowered_request
        ):
            add(path)
    for path in available:
        relative = PurePosixPath(path)
        if (
            relative.suffix.lower() in _AIDER_SOURCE_SUFFIXES
            or relative.name.lower() in _AIDER_MANIFEST_NAMES
        ):
            add(path)
    return ordered


def build_aider_command(
    *,
    python_executable: str,
    model_name: str,
    prompt: str,
    editable_files: Iterable[str],
    edit_format: str = "diff",
    api_timeout_seconds: float = 300.0,
    reasoning_effort: str | None = "low",
) -> list[str]:
    """Build the non-interactive Aider command with explicit edit targets."""

    command = [
        python_executable,
        "-m",
        "aider",
        "--model",
        f"openai/{model_name}",
        "--edit-format",
        edit_format,
        "--yes-always",
        "--no-auto-commits",
        "--no-analytics",
        "--no-check-update",
        "--no-check-model-accepts-settings",
        "--no-show-model-warnings",
        "--timeout",
        f"{min(600.0, max(30.0, api_timeout_seconds)):g}",
        "--no-gitignore",
        "--no-add-gitignore-files",
        "--map-tokens",
        "1024",
        "--input-history-file",
        os.devnull,
        "--chat-history-file",
        os.devnull,
        "--llm-history-file",
        os.devnull,
        "--read",
        ".oxygent/WORKFLOW_CONTEXT.md",
        "--no-pretty",
        "--no-stream",
        "--message",
        prompt,
    ]
    if reasoning_effort in {"minimal", "low", "medium", "high"}:
        command.extend(["--reasoning-effort", reasoning_effort])
    for editable_file in editable_files:
        command.extend(["--file", editable_file])
    return command


class CodeStageStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CodeStageVerificationStatus(str, Enum):
    NOT_STARTED = "notStarted"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"


class CodeStageReviewStatus(str, Enum):
    NOT_STARTED = "notStarted"
    RUNNING = "running"
    APPROVED = "approved"
    BASICALLY_QUALIFIED = "basicallyQualified"
    CHANGES_REQUESTED = "changesRequested"
    FAILED = "failed"


class CodeStageApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"


class SourceWorkspace(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=160)
    root_path: str = Field(min_length=1, exclude=True)
    file_count: int = Field(default=0, ge=0)
    selected_file_count: int = Field(default=0, ge=0)
    skipped_file_count: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SourceWorkspaceAnalysis(PlatformModel):
    """Structured, model-generated understanding of an imported project copy."""

    id: str = Field(default_factory=generate_uuid)
    project_id: str = Field(min_length=1)
    source_workspace_id: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=6000)
    project_type: str = Field(default="", max_length=300)
    technologies: list[str] = Field(default_factory=list, max_length=80)
    architecture: list[str] = Field(default_factory=list, max_length=80)
    main_features: list[str] = Field(default_factory=list, max_length=100)
    key_files: list[str] = Field(default_factory=list, max_length=100)
    risks: list[str] = Field(default_factory=list, max_length=100)
    suggested_focus: list[str] = Field(default_factory=list, max_length=100)
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class SourceWorkspaceCreate(PlatformModel):
    name: str = Field(default="空白项目", min_length=1, max_length=160)


class CodeStageRunRequest(PlatformModel):
    source_workspace_id: str = Field(min_length=1)
    instructions: str = Field(min_length=1, max_length=20_000)
    workflow_run_id: str | None = Field(default=None, max_length=160)
    parent_run_id: str | None = Field(default=None, max_length=160)


class CodeStageRun(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str = Field(min_length=1)
    source_workspace_id: str = Field(min_length=1)
    workflow_run_id: str | None = None
    parent_run_id: str | None = None
    task_id: str = Field(default_factory=generate_uuid)
    instructions: str = Field(min_length=1, max_length=20_000)
    status: CodeStageStatus = CodeStageStatus.QUEUED
    provider_id: str | None = None
    model_id: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    summary: str = ""
    failure_reason: str = ""
    base_commit: str = Field(default="", exclude=True)
    run_path: str = Field(default="", exclude=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CodeStageVerificationBatch(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str
    code_stage_run_id: str
    status: CodeStageVerificationStatus
    content_hash: str = Field(min_length=64, max_length=64)
    command_run_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CodeStageReviewFinding(PlatformModel):
    severity: str = Field(default="info", max_length=40)
    message: str = Field(min_length=1, max_length=4000)


class CodeStageReview(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str
    code_stage_run_id: str
    status: CodeStageReviewStatus
    approved: bool = False
    summary: str = Field(default="", max_length=8000)
    findings: list[CodeStageReviewFinding] = Field(default_factory=list, max_length=100)
    required_changes: list[str] = Field(default_factory=list, max_length=100)
    provider_id: str = ""
    model_id: str = ""
    human_override: bool = False
    override_actor_id: str = Field(default="", max_length=160)
    override_reason: str = Field(default="", max_length=2000)
    overridden_at: datetime | None = None
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CodeStageApproval(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str
    code_stage_run_id: str
    status: CodeStageApprovalStatus = CodeStageApprovalStatus.PENDING
    actor_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="", max_length=2000)
    content_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=utc_now)


class CodeStageApprovalRequest(PlatformModel):
    actor_id: str = Field(default="local-user", min_length=1, max_length=160)
    reason: str = Field(default="", max_length=2000)


class CodeStageReviewOverrideRequest(PlatformModel):
    actor_id: str = Field(default="local-user", min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=2000)


class InMemorySourceWorkspaceStore:
    def __init__(self) -> None:
        self._items: dict[str, SourceWorkspace] = {}
        self._lock = asyncio.Lock()

    async def create(self, item: SourceWorkspace) -> SourceWorkspace:
        async with self._lock:
            self._items[item.id] = item
        return item

    async def get(self, item_id: str) -> SourceWorkspace:
        async with self._lock:
            try:
                return self._items[item_id]
            except KeyError as exc:
                raise KeyError(f"source workspace not found: {item_id}") from exc

    async def list(self, project_id: str) -> list[SourceWorkspace]:
        async with self._lock:
            values = [
                item for item in self._items.values() if item.project_id == project_id
            ]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)


class InMemorySourceWorkspaceAnalysisStore:
    def __init__(self) -> None:
        self._items: dict[str, SourceWorkspaceAnalysis] = {}
        self._lock = asyncio.Lock()

    async def create(self, item: SourceWorkspaceAnalysis) -> SourceWorkspaceAnalysis:
        async with self._lock:
            self._items[item.id] = item
        return item

    async def get(self, item_id: str) -> SourceWorkspaceAnalysis:
        async with self._lock:
            try:
                return self._items[item_id]
            except KeyError as exc:
                raise KeyError(f"source analysis not found: {item_id}") from exc

    async def list(self, project_id: str) -> list[SourceWorkspaceAnalysis]:
        async with self._lock:
            values = [
                item for item in self._items.values() if item.project_id == project_id
            ]
        return sorted(values, key=lambda item: item.created_at, reverse=True)


class InMemoryCodeStageRunStore:
    def __init__(self) -> None:
        self._items: dict[str, CodeStageRun] = {}
        self._lock = asyncio.Lock()

    async def create(self, item: CodeStageRun) -> CodeStageRun:
        async with self._lock:
            self._items[item.id] = item
        return item

    async def update(self, item: CodeStageRun) -> CodeStageRun:
        async with self._lock:
            if item.id not in self._items:
                raise KeyError(f"code stage run not found: {item.id}")
            self._items[item.id] = item
        return item

    async def get(self, item_id: str) -> CodeStageRun:
        async with self._lock:
            try:
                return self._items[item_id]
            except KeyError as exc:
                raise KeyError(f"code stage run not found: {item_id}") from exc

    async def list(self, project_id: str) -> list[CodeStageRun]:
        async with self._lock:
            values = [
                item for item in self._items.values() if item.project_id == project_id
            ]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)


class SourceWorkspaceManager:
    """Own managed copies of user-selected folders; original files are untouched."""

    def __init__(
        self,
        root: Path,
        *,
        max_files: int = 3000,
        max_total_bytes: int = 80_000_000,
        max_file_bytes: int = 8_000_000,
    ) -> None:
        self.root = root.resolve()
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes
        self.max_file_bytes = max_file_bytes

    @staticmethod
    def accept_path(raw_path: str) -> str | None:
        path = normalize_relative_path(raw_path)
        parts = PurePosixPath(path).parts
        lowered = [part.lower() for part in parts]
        name = lowered[-1]
        if (
            any(
                part in IGNORED_SOURCE_PARTS or part.startswith(".aider")
                for part in lowered
            )
            or name in _IGNORED_SOURCE_NAMES
            or name == ".env"
            or name.startswith(".env.")
            or name.endswith((".key", ".pem", ".p12", ".pfx"))
        ):
            return None
        return path

    @staticmethod
    def build_analysis_context(source: SourceWorkspace) -> str:
        """Build a bounded, text-only project snapshot for model analysis."""

        root = Path(source.root_path).resolve()
        files = repository_file_list(root)
        manifests = {
            "readme",
            "readme.md",
            "readme.rst",
            "package.json",
            "pyproject.toml",
            "requirements.txt",
            "cargo.toml",
            "go.mod",
            "pom.xml",
            "build.gradle",
            "composer.json",
            "gemfile",
            "dockerfile",
            "docker-compose.yml",
        }
        text_suffixes = {
            ".c",
            ".cc",
            ".cpp",
            ".css",
            ".go",
            ".h",
            ".hpp",
            ".html",
            ".java",
            ".js",
            ".json",
            ".jsx",
            ".kt",
            ".md",
            ".php",
            ".py",
            ".rb",
            ".rs",
            ".sh",
            ".sql",
            ".swift",
            ".toml",
            ".ts",
            ".tsx",
            ".vue",
            ".xml",
            ".yaml",
            ".yml",
        }

        def priority(path: str) -> tuple[int, int, str]:
            relative = PurePosixPath(path)
            name = relative.name.lower()
            if name in manifests:
                return (0, len(relative.parts), path)
            if relative.suffix.lower() in text_suffixes:
                return (1, len(relative.parts), path)
            return (2, len(relative.parts), path)

        excerpts: list[str] = []
        used = 0
        for relative_path in sorted(files, key=priority):
            relative = PurePosixPath(relative_path)
            if (
                relative.name.lower() not in manifests
                and relative.suffix.lower() not in text_suffixes
            ):
                continue
            target = (root / relative_path).resolve()
            try:
                data = target.read_bytes()[:20_000]
            except OSError:
                continue
            if b"\x00" in data:
                continue
            text = data.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            remaining = 100_000 - used
            if remaining <= 0:
                break
            excerpt = f"\n## {relative_path}\n{text[:remaining]}"
            excerpts.append(excerpt)
            used += len(excerpt)
            if len(excerpts) >= 30:
                break
        tree = "\n".join(files[:1200])
        return f"# Project file tree ({len(files)} files shown)\n{tree}\n\n# Selected file excerpts\n{''.join(excerpts)}"

    def create(
        self,
        project_id: str,
        name: str,
        files: Iterable[tuple[str, bytes]],
    ) -> SourceWorkspace:
        workspace_id = generate_uuid()
        destination = (self.root / project_id / workspace_id).resolve()
        if not destination.is_relative_to(self.root):
            raise CodeWorkspaceError("source workspace escaped its managed root")
        destination.mkdir(parents=True, exist_ok=False)
        file_count = 0
        selected_file_count = 0
        skipped_file_count = 0
        total_bytes = 0
        imported_paths: set[str] = set()
        try:
            for raw_path, content in files:
                selected_file_count += 1
                path = self.accept_path(raw_path)
                if path is None:
                    skipped_file_count += 1
                    continue
                if path in imported_paths:
                    skipped_file_count += 1
                    continue
                if len(content) > self.max_file_bytes:
                    raise CodeWorkspaceError(f"file is too large to import: {path}")
                file_count += 1
                total_bytes += len(content)
                if file_count > self.max_files or total_bytes > self.max_total_bytes:
                    raise CodeWorkspaceError(
                        "uploaded project exceeds the local import limit"
                    )
                target = (destination / path).resolve()
                if not target.is_relative_to(destination):
                    raise CodeWorkspaceError(
                        "uploaded file escaped its managed workspace"
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                imported_paths.add(path)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        return SourceWorkspace(
            id=workspace_id,
            projectId=project_id,
            name=name,
            rootPath=str(destination),
            fileCount=file_count,
            selectedFileCount=selected_file_count,
            skippedFileCount=skipped_file_count,
            totalBytes=total_bytes,
        )

    def prepare_run(
        self,
        source: SourceWorkspace,
        run_id: str,
        *,
        parent_run: CodeStageRun | None = None,
    ) -> Path:
        destination = (self.root / source.project_id / "runs" / run_id).resolve()
        if not destination.is_relative_to(self.root):
            raise CodeWorkspaceError("code run escaped its managed root")
        source_path = source.root_path
        if parent_run is not None:
            if (
                parent_run.status is not CodeStageStatus.COMPLETED
                or not parent_run.run_path
            ):
                raise CodeWorkspaceError(
                    "parent code run is not available for revision"
                )
            source_path = parent_run.run_path
        shutil.copytree(
            source_path,
            destination,
            ignore=shutil.ignore_patterns(".git", ".aider*", "__pycache__"),
        )
        return destination

    @staticmethod
    def read_output_file(run: CodeStageRun, relative_path: str) -> bytes:
        path = normalize_relative_path(relative_path)
        if path not in run.changed_files:
            raise CodeWorkspaceError(
                "only generated or changed files can be downloaded"
            )
        root = Path(run.run_path).resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise CodeWorkspaceError("generated file is unavailable")
        return target.read_bytes()

    @staticmethod
    def build_archive(run: CodeStageRun, archive_root: Path) -> Path:
        if run.status is not CodeStageStatus.COMPLETED:
            raise CodeWorkspaceError("code result is not ready for download")
        archive_root.mkdir(parents=True, exist_ok=True)
        archive = archive_root / f"oxygent-code-{run.id}.zip"
        root = Path(run.run_path).resolve()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in sorted(root.rglob("*")):
                relative_parts = path.relative_to(root).parts
                if not path.is_file() or any(
                    part in {".git", ".oxygent"} for part in relative_parts
                ):
                    continue
                if any(part.startswith(".aider") for part in relative_parts):
                    continue
                output.write(path, path.relative_to(root).as_posix())
        return archive
