"""Project and ProjectTask domain records plus persistence boundaries."""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from pydantic import Field, field_validator

from oxygent.utils.common_utils import generate_uuid

from .common import PlatformModel, utc_now


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class ProjectTaskStatus(str, Enum):
    READY = "ready"
    IN_PROGRESS = "inProgress"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class ProjectTaskRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Project(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    status: ProjectStatus = ProjectStatus.ACTIVE
    repository: str | None = Field(default=None, max_length=500)
    team: list[str] = Field(default_factory=list, max_length=50)
    active_tasks: int = Field(default=0, ge=0)
    monthly_cost: float = Field(default=0.0, ge=0)
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_activity_at: datetime = Field(default_factory=utc_now)


class ProjectCreate(PlatformModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=4000)
    repository: str | None = Field(default=None, max_length=500)
    team: list[str] = Field(default_factory=list, max_length=50)
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(PlatformModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    status: ProjectStatus | None = None
    repository: str | None = Field(default=None, max_length=500)
    team: list[str] | None = Field(default=None, max_length=50)
    settings: dict[str, Any] | None = None


class ProjectTask(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=4000)
    task_type: str = Field(default="general", min_length=1, max_length=80)
    status: ProjectTaskStatus = ProjectTaskStatus.READY
    risk: ProjectTaskRisk = ProjectTaskRisk.MEDIUM
    source_trace_id: str | None = Field(default=None, max_length=200)
    attachment_references: list[str] = Field(default_factory=list, max_length=100)
    source_artifact_ids: list[str] = Field(default_factory=list, max_length=100)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("attachment_references")
    @classmethod
    def validate_attachment_references(cls, values: list[str]) -> list[str]:
        clean: list[str] = []
        for value in values:
            if not value or len(value) > 255:
                raise ValueError("attachment references must be 1-255 characters")
            if "/" in value or "\\" in value or ".." in value:
                raise ValueError("attachment references must be opaque file names")
            if value not in clean:
                clean.append(value)
        return clean


class ProjectTaskFromChat(PlatformModel):
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=4000)
    task_type: str = Field(default="general", min_length=1, max_length=80)
    risk: ProjectTaskRisk = ProjectTaskRisk.MEDIUM
    source_trace_id: str | None = Field(default=None, max_length=200)
    attachment_references: list[str] = Field(default_factory=list, max_length=100)
    source_artifact_ids: list[str] = Field(default_factory=list, max_length=100)

    _validate_attachment_references = field_validator("attachment_references")(
        ProjectTask.validate_attachment_references.__func__
    )


class ProjectRepository(Protocol):
    async def create(self, project: Project) -> Project: ...

    async def get(self, project_id: str) -> Project: ...

    async def list(self) -> list[Project]: ...

    async def update(self, project: Project) -> Project: ...

    async def delete(self, project_id: str) -> None: ...


class ProjectTaskRepository(Protocol):
    async def create(self, task: ProjectTask) -> ProjectTask: ...

    async def get(self, task_id: str) -> ProjectTask: ...

    async def list(self, project_id: str | None = None) -> list[ProjectTask]: ...


class InMemoryProjectRepository:
    """Concurrency-safe in-process Project repository for demos and tests."""

    def __init__(self, projects: list[Project] | None = None) -> None:
        self._projects = {project.id: project for project in projects or []}
        self._lock = asyncio.Lock()

    async def create(self, project: Project) -> Project:
        async with self._lock:
            if project.id in self._projects:
                raise ValueError(f"project already exists: {project.id}")
            self._projects[project.id] = project
        return project

    async def get(self, project_id: str) -> Project:
        async with self._lock:
            try:
                return self._projects[project_id]
            except KeyError as exc:
                raise KeyError(f"project not found: {project_id}") from exc

    async def list(self) -> list[Project]:
        async with self._lock:
            values = list(self._projects.values())
        return sorted(values, key=lambda item: item.last_activity_at, reverse=True)

    async def update(self, project: Project) -> Project:
        async with self._lock:
            if project.id not in self._projects:
                raise KeyError(f"project not found: {project.id}")
            self._projects[project.id] = project
        return project

    async def delete(self, project_id: str) -> None:
        async with self._lock:
            if project_id not in self._projects:
                raise KeyError(f"project not found: {project_id}")
            del self._projects[project_id]


class InMemoryProjectTaskRepository:
    """Concurrency-safe in-process ProjectTask repository."""

    def __init__(self, tasks: list[ProjectTask] | None = None) -> None:
        self._tasks = {task.id: task for task in tasks or []}
        self._lock = asyncio.Lock()

    async def create(self, task: ProjectTask) -> ProjectTask:
        async with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"project task already exists: {task.id}")
            self._tasks[task.id] = task
        return task

    async def get(self, task_id: str) -> ProjectTask:
        async with self._lock:
            try:
                return self._tasks[task_id]
            except KeyError as exc:
                raise KeyError(f"project task not found: {task_id}") from exc

    async def list(self, project_id: str | None = None) -> list[ProjectTask]:
        async with self._lock:
            values = list(self._tasks.values())
        if project_id is not None:
            values = [item for item in values if item.project_id == project_id]
        return sorted(values, key=lambda item: item.created_at, reverse=True)


class EsDocumentStore(Protocol):
    """Subset shared by LocalEs and Elasticsearch implementations."""

    async def index(
        self, index_name: str, doc_id: str, body: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def search(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]: ...

    async def delete(self, index_name: str, doc_id: str) -> dict[str, Any]: ...


class LocalEsProjectRepository:
    """Project repository adapter compatible with OxyGent's LocalEs API."""

    index_name = "platform_projects"

    def __init__(self, backend: EsDocumentStore) -> None:
        self.backend = backend

    async def create(self, project: Project) -> Project:
        try:
            await self.get(project.id)
        except KeyError:
            await self.backend.index(
                self.index_name,
                project.id,
                project.model_dump(mode="json", by_alias=True),
            )
            return project
        raise ValueError(f"project already exists: {project.id}")

    async def get(self, project_id: str) -> Project:
        matches = await _search_documents(self.backend, self.index_name, project_id)
        if not matches:
            raise KeyError(f"project not found: {project_id}")
        return Project.model_validate(matches[0])

    async def list(self) -> list[Project]:
        values = [
            Project.model_validate(item)
            for item in await _search_documents(self.backend, self.index_name)
        ]
        return sorted(values, key=lambda item: item.last_activity_at, reverse=True)

    async def update(self, project: Project) -> Project:
        await self.get(project.id)
        await self.backend.index(
            self.index_name,
            project.id,
            project.model_dump(mode="json", by_alias=True),
        )
        return project

    async def delete(self, project_id: str) -> None:
        await self.get(project_id)
        await self.backend.delete(self.index_name, project_id)


class LocalEsProjectTaskRepository:
    """ProjectTask repository adapter compatible with OxyGent's LocalEs API."""

    index_name = "platform_project_tasks"

    def __init__(self, backend: EsDocumentStore) -> None:
        self.backend = backend

    async def create(self, task: ProjectTask) -> ProjectTask:
        try:
            await self.get(task.id)
        except KeyError:
            await self.backend.index(
                self.index_name,
                task.id,
                task.model_dump(mode="json", by_alias=True),
            )
            return task
        raise ValueError(f"project task already exists: {task.id}")

    async def get(self, task_id: str) -> ProjectTask:
        matches = await _search_documents(self.backend, self.index_name, task_id)
        if not matches:
            raise KeyError(f"project task not found: {task_id}")
        return ProjectTask.model_validate(matches[0])

    async def list(self, project_id: str | None = None) -> list[ProjectTask]:
        values = [
            ProjectTask.model_validate(item)
            for item in await _search_documents(self.backend, self.index_name)
        ]
        if project_id is not None:
            values = [item for item in values if item.project_id == project_id]
        return sorted(values, key=lambda item: item.created_at, reverse=True)


async def _search_documents(
    backend: EsDocumentStore, index_name: str, document_id: str | None = None
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"match_all": {}}
    if document_id is not None:
        query = {"term": {"id": document_id}}
    response = await backend.search(index_name, {"query": query, "size": 10000})
    return [hit["_source"] for hit in response.get("hits", {}).get("hits", [])]
