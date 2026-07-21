"""Application services for Project and Artifact product APIs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from oxygent.utils.common_utils import generate_uuid

from .artifacts import ArtifactBase, InMemoryArtifactStore, ValidationStatus
from .common import PlatformModel, utc_now
from .control_plane import PlatformControlPlane
from .coding import (
    CodeTask,
    CodeTaskCreate,
    CodingEngine,
    CodingOperation,
    CodingRunRequest,
    CodingRunResult,
    InMemoryCodeTaskStore,
    InMemoryRepositoryProfileStore,
    NativeCodingEngine,
    RepositoryProfile,
    RepositoryRegistration,
    ScopeGuard,
    ScopeViolation,
    WorktreeManager,
    run_git,
)
from .projects import (
    InMemoryProjectRepository,
    InMemoryProjectTaskRepository,
    Project,
    ProjectCreate,
    ProjectRepository,
    ProjectTask,
    ProjectTaskFromChat,
    ProjectTaskRepository,
    ProjectUpdate,
)


class ProjectActivity(PlatformModel):
    id: str
    project_id: str
    event_type: str
    summary: str
    entity_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)


@dataclass
class PlatformServices:
    """Non-global service container passed explicitly to the FastAPI router."""

    projects: ProjectRepository = field(default_factory=InMemoryProjectRepository)
    tasks: ProjectTaskRepository = field(default_factory=InMemoryProjectTaskRepository)
    artifacts: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)
    control_plane: PlatformControlPlane = field(default_factory=PlatformControlPlane)
    repositories: InMemoryRepositoryProfileStore = field(
        default_factory=InMemoryRepositoryProfileStore
    )
    code_tasks: InMemoryCodeTaskStore = field(default_factory=InMemoryCodeTaskStore)
    coding_engine: CodingEngine = field(default_factory=NativeCodingEngine)
    worktrees: WorktreeManager | None = None
    code_authorization_enabled: bool = False
    _activities: list[ProjectActivity] = field(default_factory=list, init=False)
    _activity_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @classmethod
    def with_code_workspace(
        cls,
        *,
        repository_roots: dict[str, Path],
        workspace_root: Path,
        code_authorization_enabled: bool = False,
        **kwargs: Any,
    ) -> PlatformServices:
        """Build services with an explicit repository allow-list and worktree root."""
        return cls(
            worktrees=WorktreeManager(repository_roots, workspace_root),
            code_authorization_enabled=code_authorization_enabled,
            **kwargs,
        )

    @property
    def code_workspace_configured(self) -> bool:
        return bool(self.worktrees and self.worktrees.configured)

    async def create_project(self, payload: ProjectCreate) -> Project:
        project = Project(**payload.model_dump())
        await self.projects.create(project)
        await self._record_activity(project.id, "project.created", "Project created")
        return project

    async def update_project(self, project_id: str, payload: ProjectUpdate) -> Project:
        project = await self.projects.get(project_id)
        updates = {
            key: value
            for key, value in payload.model_dump().items()
            if key in payload.model_fields_set
        }
        now = utc_now()
        updated = project.model_copy(
            update={**updates, "updated_at": now, "last_activity_at": now}
        )
        await self.projects.update(updated)
        await self._record_activity(project_id, "project.updated", "Project updated")
        return updated

    async def delete_project(self, project_id: str) -> None:
        await self.projects.get(project_id)
        if await self.tasks.list(project_id) or self.artifacts.list(project_id):
            raise ValueError("only empty projects can be deleted; archive this project")
        await self.projects.delete(project_id)

    async def create_task_from_chat(
        self, project_id: str, payload: ProjectTaskFromChat
    ) -> ProjectTask:
        project = await self.projects.get(project_id)
        self._validate_source_artifacts(project_id, payload.source_artifact_ids)
        task = ProjectTask(project_id=project_id, **payload.model_dump())
        await self.tasks.create(task)
        now = utc_now()
        await self.projects.update(
            project.model_copy(
                update={
                    "active_tasks": project.active_tasks + 1,
                    "updated_at": now,
                    "last_activity_at": now,
                }
            )
        )
        await self._record_activity(
            project_id,
            "task.createdFromChat",
            f"Task created from Chat: {task.title}",
            task.id,
        )
        return task

    async def register_repository(
        self, project_id: str, payload: RepositoryRegistration
    ) -> RepositoryProfile:
        await self.projects.get(project_id)
        if not self.worktrees:
            raise ValueError("Code Workspace is not configured")
        self.worktrees.resolve_repository(payload.root_reference)
        for branch in payload.allowed_base_branches:
            root = self.worktrees.resolve_repository(payload.root_reference)
            await run_git(root, "rev-parse", "--verify", f"{branch}^{{commit}}")
        profile = RepositoryProfile(project_id=project_id, **payload.model_dump())
        await self.repositories.create(profile)
        await self._touch_project(project_id)
        await self._record_activity(
            project_id,
            "repository.registered",
            f"Repository registered: {profile.name}",
            profile.id,
        )
        return profile

    async def create_code_task(
        self, project_id: str, payload: CodeTaskCreate
    ) -> CodeTask:
        await self.projects.get(project_id)
        repository = await self.repositories.get(payload.repository_id)
        if repository.project_id != project_id:
            raise ValueError("repository must belong to the target project")
        if not repository.enabled:
            raise ValueError("repository is disabled")
        if payload.project_task_id:
            project_task = await self.tasks.get(payload.project_task_id)
            if project_task.project_id != project_id:
                raise ValueError("Project Task must belong to the target project")
        if not self.worktrees:
            raise ValueError("Code Workspace is not configured")
        task_id = generate_uuid()
        base_branch = payload.base_branch or repository.default_branch
        base_commit, branch, worktree = await self.worktrees.create_worktree(
            repository, task_id, base_branch
        )
        task = CodeTask(
            id=task_id,
            projectId=project_id,
            projectTaskId=payload.project_task_id,
            repositoryId=repository.id,
            baseBranch=base_branch,
            baseCommit=base_commit,
            branch=branch,
            worktreePath=str(worktree),
            changeContract=payload.change_contract,
        )
        await self.code_tasks.create(task)
        await self._touch_project(project_id)
        await self._record_activity(
            project_id,
            "codeTask.worktreeCreated",
            f"Isolated worktree created for {task.branch}",
            task.id,
        )
        return task

    async def execute_code_read(
        self,
        project_id: str,
        task_id: str,
        *,
        operation: CodingOperation,
        path: str | None = None,
        query: str | None = None,
        max_results: int = 200,
        max_output_bytes: int = 256_000,
    ) -> CodingRunResult:
        task = await self.code_tasks.get(task_id)
        if task.project_id != project_id:
            raise KeyError(f"code task not found: {task_id}")
        if operation is CodingOperation.READ_FILE:
            ScopeGuard.check_path(task.change_contract, path or "")
        result = await self.coding_engine.execute(
            CodingRunRequest(
                taskId=task.id,
                operation=operation,
                worktreePath=task.worktree_path,
                baseCommit=task.base_commit,
                path=path,
                query=query,
                maxResults=max_results,
                maxOutputBytes=max_output_bytes,
            )
        )
        if operation is CodingOperation.TREE:
            files = []
            for item in result.data.get("files", []):
                try:
                    files.append(ScopeGuard.check_path(task.change_contract, item))
                except ScopeViolation:
                    continue
            result.data["files"] = files
        elif operation is CodingOperation.SEARCH:
            matches = []
            for item in result.data.get("matches", []):
                candidate = item.split(":", 1)[0]
                try:
                    ScopeGuard.check_path(task.change_contract, candidate)
                except ScopeViolation:
                    continue
                matches.append(item)
            result.data["matches"] = matches
        return result

    async def list_artifacts(
        self, project_id: str, *, latest_only: bool = False
    ) -> list[ArtifactBase]:
        await self.projects.get(project_id)
        values = self.artifacts.list(project_id)
        if latest_only:
            superseded = {
                artifact.supersedes_artifact_id
                for artifact in values
                if artifact.supersedes_artifact_id
            }
            values = [artifact for artifact in values if artifact.id not in superseded]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    async def revise_artifact(
        self,
        project_id: str,
        artifact_id: str,
        content: dict[str, Any],
        *,
        producer_role: str | None = None,
        producer_agent: str | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
        validation_status: ValidationStatus = ValidationStatus.UNVALIDATED,
    ) -> ArtifactBase:
        await self.projects.get(project_id)
        artifact = self.artifacts.get(artifact_id)
        if artifact.project_id != project_id:
            raise KeyError(f"artifact not found: {artifact_id}")
        revision = self.artifacts.revise(
            artifact_id,
            content,
            producer_role=producer_role,
            producer_agent=producer_agent,
            provider_id=provider_id,
            model_id=model_id,
            validation_status=validation_status,
        )
        await self._touch_project(project_id)
        await self._record_activity(
            project_id,
            "artifact.revised",
            f"{revision.type.value} revision {revision.revision} created",
            revision.id,
        )
        return revision

    async def list_activity(self, project_id: str) -> list[ProjectActivity]:
        await self.projects.get(project_id)
        async with self._activity_lock:
            values = [
                item for item in self._activities if item.project_id == project_id
            ]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    def _validate_source_artifacts(
        self, project_id: str, artifact_ids: list[str]
    ) -> None:
        for artifact_id in artifact_ids:
            try:
                artifact = self.artifacts.get(artifact_id)
            except KeyError as exc:
                raise ValueError(f"source artifact not found: {artifact_id}") from exc
            if artifact.project_id != project_id:
                raise ValueError("source artifacts must belong to the target project")

    async def _touch_project(self, project_id: str) -> None:
        project = await self.projects.get(project_id)
        now = utc_now()
        await self.projects.update(
            project.model_copy(update={"updated_at": now, "last_activity_at": now})
        )

    async def _record_activity(
        self,
        project_id: str,
        event_type: str,
        summary: str,
        entity_id: str = "",
    ) -> None:
        activity = ProjectActivity(
            id=generate_uuid(),
            project_id=project_id,
            event_type=event_type,
            summary=summary,
            entity_id=entity_id,
        )
        async with self._activity_lock:
            self._activities.append(activity)
