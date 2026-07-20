"""Additive FastAPI router for Project and Artifact product capabilities."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import Field

from oxygent.schemas import WebResponse

from .artifacts import ValidationStatus
from .common import PlatformModel
from .projects import ProjectCreate, ProjectTaskFromChat, ProjectUpdate
from .services import PlatformServices


class ArtifactRevisionRequest(PlatformModel):
    content: dict[str, Any]
    producer_role: str | None = Field(default=None, max_length=120)
    producer_agent: str | None = Field(default=None, max_length=120)
    provider_id: str | None = Field(default=None, max_length=160)
    model_id: str | None = Field(default=None, max_length=160)
    validation_status: ValidationStatus = ValidationStatus.UNVALIDATED


def _dump(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True)


def _response(**data: Any) -> dict[str, Any]:
    return WebResponse(data=data).to_dict()


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def build_platform_router(services: PlatformServices) -> APIRouter:
    """Build a router bound to an explicit, caller-owned service container."""
    router = APIRouter(prefix="/api/v1/platform", tags=["platform"])

    @router.get("/capabilities")
    async def capabilities() -> dict[str, Any]:
        return _response(
            capabilities={
                "projects": True,
                "artifacts": True,
                "chatToProjectTask": True,
                "codeWorkspace": False,
                "gitWorktrees": False,
            }
        )

    @router.get("/projects")
    async def list_projects() -> dict[str, Any]:
        projects = await services.projects.list()
        return _response(items=[_dump(project) for project in projects])

    @router.post("/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(payload: ProjectCreate) -> dict[str, Any]:
        project = await services.create_project(payload)
        return _response(project=_dump(project))

    @router.get("/projects/{project_id}")
    async def get_project(project_id: str) -> dict[str, Any]:
        try:
            project = await services.projects.get(project_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(project=_dump(project))

    @router.patch("/projects/{project_id}")
    async def update_project(project_id: str, payload: ProjectUpdate) -> dict[str, Any]:
        try:
            project = await services.update_project(project_id, payload)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(project=_dump(project))

    @router.delete("/projects/{project_id}")
    async def delete_project(project_id: str) -> dict[str, Any]:
        try:
            await services.delete_project(project_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        return _response(deletedProjectId=project_id)

    @router.get("/projects/{project_id}/tasks")
    async def list_project_tasks(project_id: str) -> dict[str, Any]:
        try:
            await services.projects.get(project_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        tasks = await services.tasks.list(project_id)
        return _response(items=[_dump(task) for task in tasks])

    @router.post(
        "/projects/{project_id}/tasks/from-chat",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_task_from_chat(
        project_id: str, payload: ProjectTaskFromChat
    ) -> dict[str, Any]:
        try:
            task = await services.create_task_from_chat(project_id, payload)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return _response(task=_dump(task))

    @router.get("/projects/{project_id}/artifacts")
    async def list_project_artifacts(
        project_id: str, latest_only: bool = Query(default=False, alias="latestOnly")
    ) -> dict[str, Any]:
        try:
            artifacts = await services.list_artifacts(
                project_id, latest_only=latest_only
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(items=[_dump(artifact) for artifact in artifacts])

    @router.get("/projects/{project_id}/artifacts/{artifact_id}")
    async def get_project_artifact(project_id: str, artifact_id: str) -> dict[str, Any]:
        try:
            await services.projects.get(project_id)
            artifact = services.artifacts.get(artifact_id)
            if artifact.project_id != project_id:
                raise KeyError(f"artifact not found: {artifact_id}")
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(artifact=_dump(artifact))

    @router.post(
        "/projects/{project_id}/artifacts/{artifact_id}/revisions",
        status_code=status.HTTP_201_CREATED,
    )
    async def revise_project_artifact(
        project_id: str,
        artifact_id: str,
        payload: ArtifactRevisionRequest,
    ) -> dict[str, Any]:
        try:
            artifact = await services.revise_artifact(
                project_id,
                artifact_id,
                payload.content,
                producer_role=payload.producer_role,
                producer_agent=payload.producer_agent,
                provider_id=payload.provider_id,
                model_id=payload.model_id,
                validation_status=payload.validation_status,
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return _response(artifact=_dump(artifact))

    @router.get("/projects/{project_id}/activity")
    async def list_project_activity(project_id: str) -> dict[str, Any]:
        try:
            activity = await services.list_activity(project_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(items=[_dump(item) for item in activity])

    return router
