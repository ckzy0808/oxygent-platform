"""Additive FastAPI router for Project and Artifact product capabilities."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import Field

from oxygent.schemas import WebResponse

from .artifacts import ValidationStatus
from .common import PlatformModel
from .control_plane import (
    PlatformControlPlane,
    ProviderCreate,
    ProviderHealthCheckRequest,
    ProviderUpdate,
)
from .projects import ProjectCreate, ProjectTaskFromChat, ProjectUpdate
from .registries import RegistryError
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


def _registry_not_found(exc: RegistryError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _safe_credential_reference(reference: str) -> str:
    if not reference:
        return ""
    if reference.startswith(("env:", "secret:", "vault:", "keychain:")):
        return reference
    return "legacy-reference:[masked]"


def _credential_mask(reference: str) -> str:
    if not reference:
        return "Not configured"
    scheme = reference.split(":", 1)[0] if ":" in reference else "reference"
    return f"{scheme}:••••••••"


def _safe_base_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname or ""
        if not parsed.scheme or not hostname:
            return "[invalid URL]"
        if parsed.port:
            hostname = f"{hostname}:{parsed.port}"
        return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))
    except ValueError:
        return "[invalid URL]"


def _safe_route_reason(reason: str) -> str:
    value = reason[:1000]
    return re.sub(
        r"(?i)(api[_-]?key|authorization|bearer|token)\s*[:=]\s*[^;\s]+",
        r"\1=[redacted]",
        value,
    )


def _provider_view(provider: Any) -> dict[str, Any]:
    data = _dump(provider)
    data["baseUrl"] = _safe_base_url(provider.base_url)
    reference = _safe_credential_reference(provider.credential_reference)
    data["credentialReference"] = reference
    data["credentialMask"] = _credential_mask(reference)
    data["credentialConfigured"] = bool(provider.credential_reference)
    return data


def _model_summary(control: PlatformControlPlane, model_id: str) -> dict[str, Any]:
    model = control.models.get(model_id)
    provider = control.providers.get(model.provider_id)
    return {
        "id": model.id,
        "displayName": model.display_name,
        "modelName": model.model_name,
        "providerId": provider.id,
        "providerName": provider.name,
        "healthStatus": model.health_status.value,
    }


def _agent_view(control: PlatformControlPlane, profile: Any) -> dict[str, Any]:
    role = control.roles.get(profile.role_id)
    policy = control.model_policies.get(profile.model_policy_id)
    tool_policy = control.tool_policies.get(profile.tool_policy_id)
    latest_usage = control.latest_usage(profile)
    selected_model_id = (
        latest_usage.model_id if latest_usage else policy.primary_model_ids[0]
    )
    model = control.models.get(selected_model_id)
    provider = control.providers.get(model.provider_id)
    route = control.latest_route(profile)
    summary = control.usage_summary(profile)
    current_status = "Ready"
    if not profile.enabled:
        current_status = "Disabled"
    elif not provider.enabled or not model.enabled:
        current_status = "Unavailable"
    elif provider.health_status.value in {"degraded", "unavailable"}:
        current_status = provider.health_status.value.capitalize()
    return {
        "id": profile.id,
        "name": profile.name,
        "agentName": profile.agent_name,
        "role": _dump(role),
        "provider": {
            "id": provider.id,
            "name": provider.name,
            "type": provider.provider_type.value,
            "healthStatus": provider.health_status.value,
        },
        "model": _model_summary(control, model.id),
        "routingMode": policy.routing_mode.value,
        "routingState": control.routing_state(profile, policy),
        "capabilities": sorted(model.capabilities),
        "toolPolicy": _dump(tool_policy),
        "currentStatus": current_status,
        "usage": summary,
        "selectionReason": _safe_route_reason(route.selection_reason)
        if route
        else "Primary model selected by the configured role policy.",
        "primaryModels": [
            _model_summary(control, model_id) for model_id in policy.primary_model_ids
        ],
        "fallbackModels": [
            _model_summary(control, model_id) for model_id in policy.fallback_model_ids
        ],
    }


def build_platform_router(services: PlatformServices) -> APIRouter:
    """Build a router bound to an explicit, caller-owned service container."""
    router = APIRouter(prefix="/api/v1/platform", tags=["platform"])

    @router.get("/capabilities")
    async def capabilities() -> dict[str, Any]:
        control = services.control_plane
        return _response(
            capabilities={
                "projects": True,
                "artifacts": True,
                "chatToProjectTask": True,
                "codeWorkspace": False,
                "gitWorktrees": False,
                "agents": True,
                "models": True,
                "controlPlaneConfigured": control.configured,
                "providerMutations": control.allow_provider_mutations,
            }
        )

    @router.get("/roles")
    async def list_roles() -> dict[str, Any]:
        return _response(
            items=[_dump(role) for role in services.control_plane.roles.list()]
        )

    @router.get("/agents")
    async def list_agents() -> dict[str, Any]:
        control = services.control_plane
        try:
            items = [_agent_view(control, profile) for profile in control.agents.list()]
        except RegistryError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Invalid control-plane configuration: {exc}",
            ) from exc
        return _response(items=items)

    @router.get("/tool-policies")
    async def list_tool_policies() -> dict[str, Any]:
        return _response(
            items=[
                _dump(policy) for policy in services.control_plane.tool_policies.list()
            ]
        )

    @router.get("/providers")
    async def list_providers() -> dict[str, Any]:
        return _response(
            items=[
                _provider_view(provider)
                for provider in services.control_plane.providers.list()
            ]
        )

    @router.post("/providers", status_code=status.HTTP_201_CREATED)
    async def create_provider(payload: ProviderCreate) -> dict[str, Any]:
        try:
            provider = services.control_plane.create_provider(payload)
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except RegistryError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return _response(provider=_provider_view(provider))

    @router.patch("/providers/{provider_id}")
    async def update_provider(
        provider_id: str, payload: ProviderUpdate
    ) -> dict[str, Any]:
        try:
            provider = services.control_plane.update_provider(provider_id, payload)
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except RegistryError as exc:
            raise _registry_not_found(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return _response(provider=_provider_view(provider))

    @router.post("/providers/{provider_id}/test-connection")
    async def test_provider_connection(
        provider_id: str, payload: ProviderHealthCheckRequest
    ) -> dict[str, Any]:
        try:
            provider, model, result = await services.control_plane.health_check(
                provider_id, payload.model_id
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc
        except RegistryError as exc:
            raise _registry_not_found(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return _response(
            provider=_provider_view(provider),
            model=_model_summary(services.control_plane, model.id),
            health={
                "status": result.status.value,
                "latencyMs": result.latency_ms,
                "message": "Connection succeeded"
                if result.status.value == "healthy"
                else "Connection failed",
            },
        )

    @router.get("/models")
    async def list_models() -> dict[str, Any]:
        control = services.control_plane
        policies = control.model_policies.list()
        items = []
        for model in control.models.list():
            data = _dump(model)
            provider = control.providers.get(model.provider_id)
            data["providerName"] = provider.name
            data["assignedRoles"] = [
                control.roles.get(policy.role_id).name
                for policy in policies
                if model.id in {*policy.primary_model_ids, *policy.fallback_model_ids}
            ]
            items.append(data)
        return _response(items=items)

    @router.get("/routing-policies")
    async def list_routing_policies() -> dict[str, Any]:
        control = services.control_plane
        items = []
        for policy in control.model_policies.list():
            data = _dump(policy)
            data["role"] = _dump(control.roles.get(policy.role_id))
            data["primaryModels"] = [
                _model_summary(control, model_id)
                for model_id in policy.primary_model_ids
            ]
            data["fallbackModels"] = [
                _model_summary(control, model_id)
                for model_id in policy.fallback_model_ids
            ]
            items.append(data)
        return _response(items=items)

    @router.get("/usage")
    async def list_usage() -> dict[str, Any]:
        records = sorted(
            services.control_plane.usage.list(),
            key=lambda item: item.created_at,
            reverse=True,
        )
        items = []
        for record in records:
            data = _dump(record)
            data["failureReason"] = (
                "Provider call failed" if record.failure_reason else None
            )
            items.append(data)
        return _response(
            items=items,
            summary={
                "inputTokens": sum(record.input_tokens for record in records),
                "outputTokens": sum(record.output_tokens for record in records),
                "estimatedCost": sum(record.estimated_cost for record in records),
                "invocations": len(records),
            },
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
