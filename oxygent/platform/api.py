"""Additive FastAPI router for Project and Artifact product capabilities."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import Field

from oxygent.schemas import WebResponse

from .artifacts import ValidationStatus
from .approvals import (
    ApplyChangesRequest,
    ApprovalActionRequest,
    DiscardChangesRequest,
)
from .common import PlatformModel
from .coding import (
    CodeTaskCreate,
    CodeWorkspaceError,
    CodingOperation,
    RepositoryRegistration,
    ScopeViolation,
)
from .code_stage import (
    CodeStageApprovalRequest,
    CodeStageReviewOverrideRequest,
    CodeStageRunRequest,
    SourceWorkspaceCreate,
)
from .control_plane import (
    PlatformControlPlane,
    ProviderCreate,
    ProviderHealthCheckRequest,
    ProviderUpdate,
)
from .insights import (
    InsightDimension,
    InsightsQuery,
    aggregate_usage,
    breakdown_usage,
    filter_usage,
)
from .projects import ProjectCreate, ProjectTaskFromChat, ProjectUpdate
from .provider_adapters import ProviderCallError
from .registries import RegistryError
from .services import PlatformServices
from .tracing import EngineeringStatus
from .usage import InvocationStatus
from .verification import VerificationProfileCreate
from .workflow_runtime import WorkflowLaunchRequest


class ArtifactRevisionRequest(PlatformModel):
    content: dict[str, Any]
    producer_role: str | None = Field(default=None, max_length=120)
    producer_agent: str | None = Field(default=None, max_length=120)
    provider_id: str | None = Field(default=None, max_length=160)
    model_id: str | None = Field(default=None, max_length=160)
    validation_status: ValidationStatus = ValidationStatus.UNVALIDATED


class VerificationRunRequest(PlatformModel):
    profile_id: str = Field(min_length=1, max_length=160)
    command_id: str = Field(min_length=1, max_length=160)


class CodePreviewRequest(PlatformModel):
    instructions: str = Field(default="", max_length=4000)


def _dump(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True)


def _response(**data: Any) -> dict[str, Any]:
    return WebResponse(data=data).to_dict()


def _not_found(exc: KeyError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _registry_not_found(exc: RegistryError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _is_loopback_request(request: Request) -> bool:
    """Trust the socket peer only; forwarded headers are intentionally ignored."""
    if request.client is None:
        return False
    host = request.client.host
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _require_code_access(request: Request, services: PlatformServices) -> None:
    if services.code_authorization_enabled or _is_loopback_request(request):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Code Workspace requires loopback access or authorization middleware",
    )


def _code_workspace_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ScopeViolation):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


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
    value = re.sub(r"(?i)\bbearer\s+[^;\s]+", "Bearer [redacted]", reason[:1000])
    return re.sub(
        r"(?i)(api[_-]?key|authorization|bearer|token)\s*[:=]\s*[^;\s]+",
        r"\1=[redacted]",
        value,
    )


_WORKFLOW_PAYLOAD_FIELDS = {
    "artifact",
    "durationMs",
    "exitCode",
    "message",
    "runName",
    "status",
    "summary",
    "toolName",
    "toolsUsed",
    "inputTokens",
    "outputTokens",
    "tokenCountMethod",
}


def _safe_workflow_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return only product-safe event metadata, never prompts or raw output."""
    safe: dict[str, Any] = {}
    for key in _WORKFLOW_PAYLOAD_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if key in {"summary", "message", "runName", "toolName"}:
            safe[key] = _safe_route_reason(str(value))[:2000]
        elif key == "toolsUsed" and isinstance(value, list):
            safe[key] = [_safe_route_reason(str(item))[:160] for item in value[:50]]
        elif key == "artifact" and isinstance(value, dict):
            safe[key] = {
                field: _safe_route_reason(str(value[field]))[:300]
                for field in ("id", "type", "schemaVersion", "validationStatus")
                if field in value
            }
        elif key == "status" and value in {
            status_value.value for status_value in EngineeringStatus
        }:
            safe[key] = value
        elif (
            key in {"durationMs", "inputTokens", "outputTokens"}
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            safe[key] = max(0, value)
        elif key == "tokenCountMethod" and value in {
            "exact",
            "tiktoken",
            "approximate",
        }:
            safe[key] = value
        elif (
            key == "exitCode" and isinstance(value, int) and not isinstance(value, bool)
        ):
            safe[key] = value
    return safe


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


def _workflow_run_view(control: PlatformControlPlane, run: Any) -> dict[str, Any]:
    data = _dump(run)
    data["name"] = _safe_route_reason(run.name)[:300]
    for stage, stage_data in zip(run.stages, data["stages"]):
        stage_data["summary"] = _safe_route_reason(stage.summary)[:2000]
        stage_data["toolsUsed"] = [
            _safe_route_reason(str(tool))[:160] for tool in stage.tools_used[:50]
        ]
        stage_data["artifact"] = (
            _safe_workflow_payload({"artifact": stage.artifact}).get("artifact")
            if stage.artifact
            else None
        )
        stage_data["roleName"] = stage.role
        if stage.role and control.roles.has(stage.role):
            stage_data["roleName"] = control.roles.get(stage.role).name
        stage_data["providerName"] = stage.provider_id or ""
        if stage.provider_id and control.providers.has(stage.provider_id):
            stage_data["providerName"] = control.providers.get(stage.provider_id).name
        stage_data["modelName"] = stage.model_id or ""
        if stage.model_id and control.models.has(stage.model_id):
            stage_data["modelName"] = control.models.get(stage.model_id).display_name
    return data


def _workflow_event_view(control: PlatformControlPlane, event: Any) -> dict[str, Any]:
    data = _dump(event)
    data["payload"] = _safe_workflow_payload(event.payload)
    data["providerName"] = event.provider_id or ""
    if event.provider_id and control.providers.has(event.provider_id):
        data["providerName"] = control.providers.get(event.provider_id).name
    data["modelName"] = event.model_id or ""
    if event.model_id and control.models.has(event.model_id):
        data["modelName"] = control.models.get(event.model_id).display_name
    return data


def _recovery_patch_view(patch: Any) -> dict[str, Any]:
    """Return patch metadata without putting source content in ordinary views."""
    return {
        "id": patch.id,
        "projectId": patch.project_id,
        "taskId": patch.task_id,
        "baseCommit": patch.base_commit,
        "contentHash": patch.content_hash,
        "sizeBytes": len(patch.content.encode("utf-8")),
        "createdAt": patch.created_at.isoformat(),
    }


def build_platform_router(services: PlatformServices) -> APIRouter:
    """Build a router bound to an explicit, caller-owned service container."""
    router = APIRouter(prefix="/api/v1/platform", tags=["platform"])

    @router.post("/aider-proxy/v1/chat/completions")
    async def aider_chat_completions(request: Request) -> dict[str, Any]:
        """Loopback-only protocol bridge used by the local Aider subprocess."""
        _require_code_access(request, services)
        try:
            payload = await request.json()
            messages = payload.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError("Aider request requires messages")
            return await services.complete_aider_proxy(messages, payload)
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        except ProviderCallError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"configured model provider rejected the Aider request: {type(exc).__name__}",
            ) from exc

    @router.post("/aider-proxy/runs/{run_id}/v1/chat/completions")
    async def contextual_aider_chat_completions(
        run_id: str, request: Request
    ) -> dict[str, Any]:
        """Aider bridge that attributes every provider request to its code run."""
        _require_code_access(request, services)
        try:
            payload = await request.json()
            messages = payload.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError("Aider request requires messages")
            project_id = ""
            task_id = run_id
            try:
                code_run = await services.code_stage_runs.get(run_id)
                project_id = code_run.project_id
                task_id = code_run.task_id
            except KeyError:
                try:
                    code_task = await services.code_tasks.get(run_id)
                    project_id = code_task.project_id
                    task_id = code_task.id
                except KeyError:
                    pass
            return await services.complete_aider_proxy(
                messages,
                payload,
                project_id=project_id,
                task_id=task_id,
                run_id=run_id,
            )
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        except ProviderCallError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"configured model provider rejected the Aider request: {type(exc).__name__}",
            ) from exc

    @router.get("/capabilities")
    async def capabilities() -> dict[str, Any]:
        control = services.control_plane
        return _response(
            capabilities={
                "projects": True,
                "artifacts": True,
                "chatToProjectTask": True,
                "simpleCodeStage": True,
                "codeStageQualityLifecycle": True,
                "projectFolderImport": True,
                "projectSourceAnalysis": control.configured,
                "codeWorkspace": services.code_workspace_configured,
                "gitWorktrees": services.code_workspace_configured,
                "diffVerification": services.code_workspace_configured,
                "approvalLifecycle": services.code_workspace_configured,
                "agents": True,
                "models": True,
                "workflowTimeline": True,
                "workflowExecution": services.workflow_execution_configured,
                "insights": True,
                "executionDrawer": True,
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
    async def list_usage(
        project_id: str | None = Query(default=None, alias="projectId"),
        run_id: str | None = Query(default=None, alias="runId"),
    ) -> dict[str, Any]:
        records = sorted(
            (
                record
                for record in services.control_plane.usage.list()
                if (project_id is None or record.project_id == project_id)
                and (run_id is None or record.run_id == run_id)
            ),
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
                "totalTokens": sum(
                    record.input_tokens + record.output_tokens for record in records
                ),
                "exactInvocations": sum(
                    record.token_count_method.value == "exact" for record in records
                ),
                "estimatedInvocations": sum(
                    record.token_count_method.value != "exact" for record in records
                ),
                "activeInvocations": sum(
                    record.status is InvocationStatus.RUNNING for record in records
                ),
                "invocations": len(records),
            },
        )

    async def insights_records(
        *,
        project_id: str | None,
        role_id: str | None,
        provider_id: str | None,
        model_id: str | None,
        run_id: str | None,
        invocation_status: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ) -> tuple[InsightsQuery, list[Any]]:
        if project_id:
            try:
                await services.projects.get(project_id)
            except KeyError as exc:
                raise _not_found(exc) from exc
        try:
            query = InsightsQuery(
                projectId=project_id,
                roleId=role_id,
                providerId=provider_id,
                modelId=model_id,
                runId=run_id,
                status=invocation_status,
                dateFrom=date_from,
                dateTo=date_to,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        return query, filter_usage(services.control_plane.usage.list(), query)

    @router.get("/insights/summary")
    async def insights_summary(
        project_id: str | None = Query(default=None, alias="projectId"),
        role_id: str | None = Query(default=None, alias="roleId"),
        provider_id: str | None = Query(default=None, alias="providerId"),
        model_id: str | None = Query(default=None, alias="modelId"),
        run_id: str | None = Query(default=None, alias="runId"),
        invocation_status: str | None = Query(default=None, alias="status"),
        date_from: datetime | None = Query(default=None, alias="dateFrom"),
        date_to: datetime | None = Query(default=None, alias="dateTo"),
    ) -> dict[str, Any]:
        query, records = await insights_records(
            project_id=project_id,
            role_id=role_id,
            provider_id=provider_id,
            model_id=model_id,
            run_id=run_id,
            invocation_status=invocation_status,
            date_from=date_from,
            date_to=date_to,
        )
        return _response(
            totals=_dump(aggregate_usage(records)),
            range={
                "dateFrom": query.date_from.isoformat() if query.date_from else None,
                "dateTo": query.date_to.isoformat() if query.date_to else None,
                "boundary": "inclusiveStartExclusiveEnd",
            },
        )

    @router.get("/insights/breakdown")
    async def insights_breakdown(
        dimension: InsightDimension = Query(default=InsightDimension.PROJECT),
        project_id: str | None = Query(default=None, alias="projectId"),
        role_id: str | None = Query(default=None, alias="roleId"),
        provider_id: str | None = Query(default=None, alias="providerId"),
        model_id: str | None = Query(default=None, alias="modelId"),
        run_id: str | None = Query(default=None, alias="runId"),
        invocation_status: str | None = Query(default=None, alias="status"),
        date_from: datetime | None = Query(default=None, alias="dateFrom"),
        date_to: datetime | None = Query(default=None, alias="dateTo"),
    ) -> dict[str, Any]:
        _, records = await insights_records(
            project_id=project_id,
            role_id=role_id,
            provider_id=provider_id,
            model_id=model_id,
            run_id=run_id,
            invocation_status=invocation_status,
            date_from=date_from,
            date_to=date_to,
        )
        projects = {
            project.id: project.name for project in await services.projects.list()
        }
        control = services.control_plane

        def label(target: InsightDimension, key: str) -> str:
            if key == "unassigned":
                return "Unassigned"
            if target is InsightDimension.PROJECT:
                return projects.get(key, key)
            if target is InsightDimension.ROLE and control.roles.has(key):
                return control.roles.get(key).name
            if target is InsightDimension.PROVIDER and control.providers.has(key):
                return control.providers.get(key).name
            if target is InsightDimension.MODEL and control.models.has(key):
                return control.models.get(key).display_name
            return key

        rows = breakdown_usage(records, dimension, label)
        return _response(dimension=dimension.value, items=[_dump(row) for row in rows])

    @router.get("/insights/runs")
    async def insights_runs(
        project_id: str | None = Query(default=None, alias="projectId"),
        role_id: str | None = Query(default=None, alias="roleId"),
        provider_id: str | None = Query(default=None, alias="providerId"),
        model_id: str | None = Query(default=None, alias="modelId"),
        run_id: str | None = Query(default=None, alias="runId"),
        invocation_status: str | None = Query(default=None, alias="status"),
        date_from: datetime | None = Query(default=None, alias="dateFrom"),
        date_to: datetime | None = Query(default=None, alias="dateTo"),
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict[str, Any]:
        _, records = await insights_records(
            project_id=project_id,
            role_id=role_id,
            provider_id=provider_id,
            model_id=model_id,
            run_id=run_id,
            invocation_status=invocation_status,
            date_from=date_from,
            date_to=date_to,
        )
        route_decisions = services.control_plane.traces.route_decisions()
        items = []
        for record in records[:limit]:
            route = next(
                (
                    item
                    for item in reversed(route_decisions)
                    if item.run_id == record.run_id
                    and item.role_id == record.role_id
                    and item.selected_model_id == record.model_id
                ),
                None,
            )
            items.append(
                {
                    **_dump(record),
                    "failureReason": (
                        "Provider call failed" if record.failure_reason else None
                    ),
                    "selectionReason": (
                        _safe_route_reason(route.selection_reason)
                        if route
                        else "Recorded model invocation"
                    ),
                    "workflowUrl": (
                        "workflows.html?runId=" + quote(record.run_id, safe="")
                    ),
                    "modelUrl": (
                        "models.html?tab=usage&modelId="
                        + quote(record.model_id, safe="")
                    ),
                }
            )
        return _response(items=items)

    @router.get("/workflows/runs")
    async def list_workflow_runs(
        project_id: str | None = Query(default=None, alias="projectId"),
        task_id: str | None = Query(default=None, alias="taskId"),
    ) -> dict[str, Any]:
        control = services.control_plane
        runs = control.traces.workflow_runs(
            project_id=project_id,
            task_id=task_id,
        )
        return _response(items=[_workflow_run_view(control, run) for run in runs])

    @router.get("/workflows/runs/{run_id}")
    async def get_workflow_run(run_id: str) -> dict[str, Any]:
        control = services.control_plane
        events = control.traces.workflow_events(run_id=run_id)
        if not events:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow run not found",
            )
        run = control.traces.workflow_runs()
        selected = next(item for item in run if item.run_id == run_id)
        return _response(run=_workflow_run_view(control, selected))

    @router.get("/workflows/runs/{run_id}/events")
    async def list_workflow_events(run_id: str) -> dict[str, Any]:
        control = services.control_plane
        events = control.traces.workflow_events(run_id=run_id)
        if not events:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow run not found",
            )
        return _response(
            items=[_workflow_event_view(control, event) for event in events]
        )

    @router.get("/workflows/runs/{run_id}/stream")
    async def stream_workflow_events(run_id: str) -> StreamingResponse:
        control = services.control_plane
        if not control.traces.workflow_events(run_id=run_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workflow run not found",
            )

        async def event_stream():
            delivered: set[str] = set()
            idle_ticks = 0
            while True:
                events = control.traces.workflow_events(run_id=run_id)
                fresh = [event for event in events if event.event_id not in delivered]
                for event in fresh:
                    delivered.add(event.event_id)
                    safe_event = _workflow_event_view(control, event)
                    yield "data: " + json.dumps(safe_event, ensure_ascii=False) + "\n\n"
                terminal = any(
                    event.event_type
                    in {
                        "workflow.completed",
                        "workflow.failed",
                        "workflow.awaitingImplementation",
                    }
                    for event in events
                )
                if terminal and not fresh:
                    break
                idle_ticks += 1
                if idle_ticks % 40 == 0:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/projects")
    async def list_projects() -> dict[str, Any]:
        projects = await services.projects.list()
        return _response(items=[_dump(project) for project in projects])

    @router.post("/projects", status_code=status.HTTP_201_CREATED)
    async def create_project(payload: ProjectCreate) -> dict[str, Any]:
        project = await services.create_project(payload)
        return _response(project=_dump(project))

    @router.post(
        "/projects/{project_id}/workflows/runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_project_workflow(
        project_id: str, payload: WorkflowLaunchRequest
    ) -> dict[str, Any]:
        try:
            run_id = await services.start_role_workflow(project_id, payload)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        runs = services.control_plane.traces.workflow_runs(project_id=project_id)
        run = next(item for item in runs if item.run_id == run_id)
        return _response(run=_workflow_run_view(services.control_plane, run))

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

    @router.get("/code/repository-sources")
    async def list_repository_sources(request: Request) -> dict[str, Any]:
        _require_code_access(request, services)
        items = services.worktrees.sources() if services.worktrees else []
        return _response(items=[_dump(item) for item in items])

    @router.get("/projects/{project_id}/source-workspaces")
    async def list_source_workspaces(
        project_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            await services.projects.get(project_id)
            items = await services.source_workspaces.list(project_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(items=[_dump(item) for item in items])

    @router.get("/projects/{project_id}/source-analyses")
    async def list_source_analyses(project_id: str, request: Request) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            await services.projects.get(project_id)
            items = await services.source_analyses.list(project_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(items=[_dump(item) for item in items])

    @router.post(
        "/projects/{project_id}/source-workspaces/{source_workspace_id}/analyze",
        status_code=status.HTTP_201_CREATED,
    )
    async def analyze_source_workspace(
        project_id: str,
        source_workspace_id: str,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            analysis = await services.analyze_source_workspace(
                project_id, source_workspace_id
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        except ProviderCallError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        return _response(analysis=_dump(analysis))

    @router.post(
        "/projects/{project_id}/source-workspaces/blank",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_blank_source_workspace(
        project_id: str,
        payload: SourceWorkspaceCreate,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            workspace = await services.import_source_workspace(
                project_id, payload.name, []
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(sourceWorkspace=_dump(workspace))

    @router.post(
        "/projects/{project_id}/source-workspaces/import",
        status_code=status.HTTP_201_CREATED,
    )
    async def import_source_workspace(
        project_id: str,
        request: Request,
        files: list[UploadFile] = File(...),
        paths_json: str = Form(default="[]", alias="pathsJson"),
        name: str = Form(default="上传的项目"),
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            parsed_paths = json.loads(paths_json)
            if not isinstance(parsed_paths, list) or len(parsed_paths) != len(files):
                raise ValueError("uploaded file paths do not match the selected files")
            if len(files) > services.source_workspace_manager.max_files:
                raise ValueError("too many files were selected")
            imported: list[tuple[str, bytes]] = []
            for index, upload in enumerate(files):
                path = str(parsed_paths[index] or upload.filename or "")
                imported.append((path, await upload.read()))
            workspace = await services.import_source_workspace(
                project_id, name.strip() or "上传的项目", imported
            )
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="uploaded file path metadata is invalid",
            ) from exc
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        finally:
            for upload in files:
                await upload.close()
        return _response(sourceWorkspace=_dump(workspace))

    @router.get("/projects/{project_id}/code-stage-runs")
    async def list_code_stage_runs(project_id: str, request: Request) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            await services.projects.get(project_id)
            items = await services.code_stage_runs.list(project_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(items=[_dump(item) for item in items])

    @router.post(
        "/projects/{project_id}/code-stage-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_code_stage_run(
        project_id: str,
        payload: CodeStageRunRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            run = await services.start_code_stage(project_id, payload)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(run=_dump(run))

    @router.get("/projects/{project_id}/code-stage-runs/{run_id}")
    async def get_code_stage_run(
        project_id: str, run_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            run = await services.code_stage_runs.get(run_id)
            if run.project_id != project_id:
                raise KeyError(f"code stage run not found: {run_id}")
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(run=_dump(run))

    @router.get("/projects/{project_id}/code-stage-runs/{run_id}/changes")
    async def get_code_stage_changes(
        project_id: str, run_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            changes = await services.get_code_stage_changes(project_id, run_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except CodeWorkspaceError as exc:
            raise _code_workspace_error(exc) from exc
        return _response(changes=_dump(changes))

    @router.get(
        "/projects/{project_id}/code-stage-runs/{run_id}/changes/{file_path:path}"
    )
    async def get_code_stage_file_change(
        project_id: str,
        run_id: str,
        file_path: str,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            change = await services.get_code_stage_file_change(
                project_id, run_id, file_path
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except CodeWorkspaceError as exc:
            raise _code_workspace_error(exc) from exc
        return _response(change=change)

    @router.get("/projects/{project_id}/code-stage-runs/{run_id}/lifecycle")
    async def get_code_stage_lifecycle(
        project_id: str, run_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            verification, command_runs = await services.get_code_stage_verification(
                project_id, run_id
            )
            review = await services.get_code_stage_review(project_id, run_id)
            approval = await services.get_code_stage_approval(project_id, run_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(
            verification=_dump(verification) if verification else None,
            verificationRuns=[_dump(item) for item in command_runs],
            review=_dump(review) if review else None,
            approval=_dump(approval) if approval else None,
        )

    @router.post(
        "/projects/{project_id}/code-stage-runs/{run_id}/verify",
        status_code=status.HTTP_201_CREATED,
    )
    async def verify_code_stage(
        project_id: str, run_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            verification, command_runs = await services.run_code_stage_verification(
                project_id, run_id
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError, ScopeViolation) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(
            verification=_dump(verification),
            verificationRuns=[_dump(item) for item in command_runs],
        )

    @router.post(
        "/projects/{project_id}/code-stage-runs/{run_id}/review",
        status_code=status.HTTP_201_CREATED,
    )
    async def review_code_stage(
        project_id: str, run_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            review = await services.review_code_stage(project_id, run_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError, ScopeViolation) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(review=_dump(review))

    @router.post(
        "/projects/{project_id}/code-stage-runs/{run_id}/approve",
        status_code=status.HTTP_201_CREATED,
    )
    async def approve_code_stage(
        project_id: str,
        run_id: str,
        payload: CodeStageApprovalRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            approval = await services.approve_code_stage(project_id, run_id, payload)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError, ScopeViolation) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(approval=_dump(approval))

    @router.post(
        "/projects/{project_id}/code-stage-runs/{run_id}/review-override",
        status_code=status.HTTP_201_CREATED,
    )
    async def override_code_stage_review(
        project_id: str,
        run_id: str,
        payload: CodeStageReviewOverrideRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            review = await services.override_code_stage_review(
                project_id, run_id, payload
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError, ScopeViolation) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(review=_dump(review))

    @router.post(
        "/projects/{project_id}/code-stage-runs/{run_id}/review-revision",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_code_stage_review_revision(
        project_id: str, run_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            revision = await services.start_code_stage_review_revision(
                project_id, run_id
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError, ScopeViolation) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(run=_dump(revision))

    @router.get(
        "/projects/{project_id}/code-stage-runs/{run_id}/files/{file_path:path}"
    )
    async def download_code_stage_file(
        project_id: str, run_id: str, file_path: str, request: Request
    ) -> Response:
        _require_code_access(request, services)
        try:
            run = await services.code_stage_runs.get(run_id)
            if run.project_id != project_id:
                raise KeyError(f"code stage run not found: {run_id}")
            content = services.source_workspace_manager.read_output_file(run, file_path)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except CodeWorkspaceError as exc:
            raise _code_workspace_error(exc) from exc
        filename = file_path.rsplit("/", 1)[-1]
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{quote(filename)}"'
            },
        )

    @router.get("/projects/{project_id}/code-stage-runs/{run_id}/download")
    async def download_code_stage_result(
        project_id: str, run_id: str, request: Request
    ) -> FileResponse:
        _require_code_access(request, services)
        try:
            run = await services.code_stage_runs.get(run_id)
            if run.project_id != project_id:
                raise KeyError(f"code stage run not found: {run_id}")
            archive = await asyncio.to_thread(
                services.source_workspace_manager.build_archive,
                run,
                services.source_workspace_manager.root / "archives",
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except CodeWorkspaceError as exc:
            raise _code_workspace_error(exc) from exc
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=f"{run.id}-project.zip",
        )

    @router.get("/code/repositories")
    async def list_code_repositories(
        request: Request,
        project_id: str | None = Query(default=None, alias="projectId"),
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        items = await services.repositories.list(project_id)
        return _response(items=[_dump(item) for item in items])

    @router.post(
        "/projects/{project_id}/repositories",
        status_code=status.HTTP_201_CREATED,
    )
    async def register_project_repository(
        project_id: str,
        payload: RepositoryRegistration,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            repository = await services.register_repository(project_id, payload)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(repository=_dump(repository))

    @router.get("/projects/{project_id}/repositories")
    async def list_project_repositories(
        project_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            await services.projects.get(project_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        items = await services.repositories.list(project_id)
        return _response(items=[_dump(item) for item in items])

    @router.post(
        "/projects/{project_id}/code-tasks",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_project_code_task(
        project_id: str,
        payload: CodeTaskCreate,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            task = await services.create_code_task(project_id, payload)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(task=_dump(task))

    @router.get("/projects/{project_id}/code-tasks")
    async def list_project_code_tasks(
        project_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            await services.projects.get(project_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        items = await services.code_tasks.list(project_id)
        return _response(items=[_dump(item) for item in items])

    @router.get("/projects/{project_id}/code-tasks/{task_id}")
    async def get_project_code_task(
        project_id: str, task_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            task = await services.code_tasks.get(task_id)
            if task.project_id != project_id:
                raise KeyError(f"code task not found: {task_id}")
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(task=_dump(task))

    async def code_read(
        project_id: str,
        task_id: str,
        request: Request,
        operation: CodingOperation,
        *,
        path: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            result = await services.execute_code_read(
                project_id,
                task_id,
                operation=operation,
                path=path,
                query=query,
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(result=_dump(result))

    @router.get("/projects/{project_id}/code-tasks/{task_id}/repository/metadata")
    async def get_code_repository_metadata(
        project_id: str, task_id: str, request: Request
    ) -> dict[str, Any]:
        return await code_read(project_id, task_id, request, CodingOperation.METADATA)

    @router.get("/projects/{project_id}/code-tasks/{task_id}/repository/tree")
    async def get_code_repository_tree(
        project_id: str,
        task_id: str,
        request: Request,
        path: str = Query(default=".", max_length=1000),
    ) -> dict[str, Any]:
        return await code_read(
            project_id, task_id, request, CodingOperation.TREE, path=path
        )

    @router.get("/projects/{project_id}/code-tasks/{task_id}/repository/search")
    async def search_code_repository(
        project_id: str,
        task_id: str,
        request: Request,
        query: str = Query(min_length=1, max_length=500),
        path: str = Query(default=".", max_length=1000),
    ) -> dict[str, Any]:
        return await code_read(
            project_id,
            task_id,
            request,
            CodingOperation.SEARCH,
            path=path,
            query=query,
        )

    @router.get("/projects/{project_id}/code-tasks/{task_id}/repository/file")
    async def read_code_repository_file(
        project_id: str,
        task_id: str,
        request: Request,
        path: str = Query(min_length=1, max_length=1000),
    ) -> dict[str, Any]:
        return await code_read(
            project_id, task_id, request, CodingOperation.READ_FILE, path=path
        )

    @router.get("/projects/{project_id}/code-tasks/{task_id}/diff")
    async def get_code_task_diff(
        project_id: str, task_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            snapshot = await services.get_code_diff(project_id, task_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except CodeWorkspaceError as exc:
            raise _code_workspace_error(exc) from exc
        return _response(diff=_dump(snapshot))

    @router.post("/projects/{project_id}/code-tasks/{task_id}/code-preview")
    async def generate_code_preview(
        project_id: str,
        task_id: str,
        payload: CodePreviewRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            preview = await services.run_aider_implementation(
                project_id, task_id, payload.instructions
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(preview=preview)

    @router.get("/projects/{project_id}/verification-profiles")
    async def list_verification_profiles(
        project_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            await services.projects.get(project_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        items = await services.verification_profiles.list(project_id)
        return _response(items=[_dump(item) for item in items])

    @router.post(
        "/projects/{project_id}/verification-profiles",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_verification_profile(
        project_id: str,
        payload: VerificationProfileCreate,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            profile = await services.register_verification_profile(project_id, payload)
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(profile=_dump(profile))

    @router.get("/projects/{project_id}/code-tasks/{task_id}/verification-runs")
    async def list_verification_runs(
        project_id: str, task_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            task = await services.code_tasks.get(task_id)
            if task.project_id != project_id:
                raise KeyError(f"code task not found: {task_id}")
        except KeyError as exc:
            raise _not_found(exc) from exc
        runs = await services.verification_runs.list(task_id)
        return _response(items=[_dump(item) for item in runs])

    @router.post(
        "/projects/{project_id}/code-tasks/{task_id}/verification-runs",
        status_code=status.HTTP_201_CREATED,
    )
    async def run_code_task_verification(
        project_id: str,
        task_id: str,
        payload: VerificationRunRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            run = await services.run_verification(
                project_id,
                task_id,
                payload.profile_id,
                payload.command_id,
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(run=_dump(run))

    @router.get(
        "/projects/{project_id}/code-tasks/{task_id}/verification-outputs/{output_id}"
    )
    async def get_verification_output(
        project_id: str,
        task_id: str,
        output_id: str,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            output = await services.get_verification_output(
                project_id, task_id, output_id
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(output=_dump(output))

    @router.get("/projects/{project_id}/code-tasks/{task_id}/approvals")
    async def list_code_task_approvals(
        project_id: str, task_id: str, request: Request
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            task = await services.code_tasks.get(task_id)
            if task.project_id != project_id:
                raise KeyError(f"code task not found: {task_id}")
        except KeyError as exc:
            raise _not_found(exc) from exc
        records = await services.approvals.list(task_id)
        return _response(items=[_dump(record) for record in records])

    @router.post(
        "/projects/{project_id}/code-tasks/{task_id}/request-revision",
        status_code=status.HTTP_201_CREATED,
    )
    async def request_code_revision(
        project_id: str,
        task_id: str,
        payload: ApprovalActionRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            task, record = await services.request_code_revision(
                project_id, task_id, payload
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(task=_dump(task), approval=_dump(record))

    @router.post(
        "/projects/{project_id}/code-tasks/{task_id}/approve",
        status_code=status.HTTP_201_CREATED,
    )
    async def approve_code_changes(
        project_id: str,
        task_id: str,
        payload: ApprovalActionRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            task, record = await services.approve_code_changes(
                project_id, task_id, payload
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(task=_dump(task), approval=_dump(record))

    @router.post(
        "/projects/{project_id}/code-tasks/{task_id}/apply",
        status_code=status.HTTP_201_CREATED,
    )
    async def apply_code_changes(
        project_id: str,
        task_id: str,
        payload: ApplyChangesRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            task, record = await services.apply_code_changes(
                project_id, task_id, payload
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(task=_dump(task), approval=_dump(record))

    @router.post(
        "/projects/{project_id}/code-tasks/{task_id}/export-patch",
        status_code=status.HTTP_201_CREATED,
    )
    async def export_code_patch(
        project_id: str,
        task_id: str,
        payload: ApprovalActionRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            patch, record = await services.export_code_patch(
                project_id, task_id, payload
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(patch=_recovery_patch_view(patch), approval=_dump(record))

    @router.post(
        "/projects/{project_id}/code-tasks/{task_id}/discard",
        status_code=status.HTTP_201_CREATED,
    )
    async def discard_code_task(
        project_id: str,
        task_id: str,
        payload: DiscardChangesRequest,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            task, record, patch = await services.discard_code_task(
                project_id, task_id, payload
            )
        except KeyError as exc:
            raise _not_found(exc) from exc
        except (ValueError, CodeWorkspaceError) as exc:
            raise _code_workspace_error(exc) from exc
        return _response(
            task=_dump(task),
            approval=_dump(record),
            recoveryPatch=_recovery_patch_view(patch),
        )

    @router.get(
        "/projects/{project_id}/code-tasks/{task_id}/recovery-patches/{patch_id}"
    )
    async def get_recovery_patch(
        project_id: str,
        task_id: str,
        patch_id: str,
        request: Request,
    ) -> dict[str, Any]:
        _require_code_access(request, services)
        try:
            patch = await services.get_recovery_patch(project_id, task_id, patch_id)
        except KeyError as exc:
            raise _not_found(exc) from exc
        return _response(patch=_dump(patch))

    return router
