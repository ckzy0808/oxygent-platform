"""Minimal Artifact-driven Product→Architecture→Lead→Review workflow."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from oxygent.oxy.base_flow import BaseFlow
from oxygent.schemas import OxyRequest, OxyResponse, OxyState
from oxygent.utils.common_utils import generate_uuid

from .artifacts import (
    ArchitectureDecision,
    ArchitectureDecisionContent,
    RequirementSpec,
    RequirementSpecContent,
    ReviewReport,
    ReviewReportContent,
    TaskGraph,
    TaskGraphContent,
    ValidationStatus,
)
from .profiles import AgentProfile
from .tracing import (
    EngineeringStatus,
    ExecutionTrace,
    WorkflowEvent,
    WorkflowPhase,
)


class BasicRoleWorkflow(BaseFlow):
    """Sequential four-role workflow that exchanges immutable Artifacts."""

    ROLE_ORDER: ClassVar[tuple[str, ...]] = (
        "product_manager",
        "solution_architect",
        "technical_lead",
        "reviewer",
    )
    ROLE_PHASES: ClassVar[dict[str, WorkflowPhase]] = {
        "product_manager": WorkflowPhase.REQUIREMENT,
        "solution_architect": WorkflowPhase.ARCHITECTURE,
        "technical_lead": WorkflowPhase.PLAN,
        "reviewer": WorkflowPhase.REVIEW,
    }
    ACTIVE_STATUSES: ClassVar[dict[str, EngineeringStatus]] = {
        "product_manager": EngineeringStatus.ANALYZING,
        "solution_architect": EngineeringStatus.PLANNING,
        "technical_lead": EngineeringStatus.PLANNING,
        "reviewer": EngineeringStatus.REVIEWING,
    }

    agent_profiles: dict[str, AgentProfile]
    artifact_store: Any = Field(exclude=True, repr=False)
    trace_store: Any = Field(exclude=True, repr=False)
    is_master: bool = True
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Product idea"},
                "project_id": {
                    "type": "string",
                    "description": "Optional project identifier",
                },
            },
            "required": ["query"],
        }
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        missing = set(self.ROLE_ORDER) - set(self.agent_profiles)
        if missing:
            raise ValueError(f"missing AgentProfile for roles: {sorted(missing)}")
        self.add_permitted_tools(
            [self.agent_profiles[role_id].agent_name for role_id in self.ROLE_ORDER]
        )

    @staticmethod
    def _routing_context(
        profile: AgentProfile,
        *,
        project_id: str,
        task_id: str,
        run_id: str,
        task_type: str,
        producer_provider_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "projectId": project_id,
            "taskId": task_id,
            "runId": run_id,
            "roleId": profile.role_id,
            "agentId": profile.id,
            "policyId": profile.model_policy_id,
            "taskType": task_type,
            "producerProviderId": producer_provider_id,
        }

    def _append_workflow_event(self, event: WorkflowEvent) -> None:
        append = getattr(self.trace_store, "append_workflow_event", None)
        if append is not None:
            append(event)

    async def _call_role(
        self,
        oxy_request: OxyRequest,
        role_id: str,
        query: str,
        *,
        project_id: str,
        task_id: str,
        run_id: str,
        producer_provider_id: str | None = None,
    ) -> OxyResponse:
        profile = self.agent_profiles[role_id]
        phase = self.ROLE_PHASES[role_id]
        self.trace_store.append_event(
            ExecutionTrace(
                id=generate_uuid(),
                project_id=project_id,
                task_id=task_id,
                run_id=run_id,
                role_id=role_id,
                agent_id=profile.id,
                event_type="role_task",
                status="started",
            )
        )
        self._append_workflow_event(
            WorkflowEvent(
                eventId=generate_uuid(),
                projectId=project_id,
                taskId=task_id,
                runId=run_id,
                agentId=profile.id,
                role=role_id,
                phase=phase,
                eventType="phase.started",
                payload={
                    "status": self.ACTIVE_STATUSES[role_id].value,
                    "summary": f"{profile.name} started the {phase.value} phase.",
                },
            )
        )
        try:
            response = await oxy_request.call(
                callee=profile.agent_name,
                arguments={
                    "query": query,
                    "llm_params": {
                        "_routing_context": self._routing_context(
                            profile,
                            project_id=project_id,
                            task_id=task_id,
                            run_id=run_id,
                            task_type=role_id,
                            producer_provider_id=producer_provider_id,
                        )
                    },
                },
            )
        except Exception:
            self.trace_store.append_event(
                ExecutionTrace(
                    id=generate_uuid(),
                    project_id=project_id,
                    task_id=task_id,
                    run_id=run_id,
                    role_id=role_id,
                    agent_id=profile.id,
                    event_type="role_task",
                    status="failed",
                )
            )
            self._append_workflow_event(
                WorkflowEvent(
                    eventId=generate_uuid(),
                    projectId=project_id,
                    taskId=task_id,
                    runId=run_id,
                    agentId=profile.id,
                    role=role_id,
                    phase=phase,
                    eventType="phase.failed",
                    payload={
                        "status": EngineeringStatus.FAILED.value,
                        "summary": f"{profile.name} failed the {phase.value} phase.",
                    },
                )
            )
            raise
        status = "succeeded" if response.state is OxyState.COMPLETED else "failed"
        self.trace_store.append_event(
            ExecutionTrace(
                id=generate_uuid(),
                project_id=project_id,
                task_id=task_id,
                run_id=run_id,
                role_id=role_id,
                agent_id=profile.id,
                event_type="role_task",
                status=status,
                provider_id=response.extra.get("provider_id"),
                model_id=response.extra.get("model_id"),
            )
        )
        self._append_workflow_event(
            WorkflowEvent(
                eventId=generate_uuid(),
                projectId=project_id,
                taskId=task_id,
                runId=run_id,
                agentId=profile.id,
                role=role_id,
                providerId=response.extra.get("provider_id"),
                modelId=response.extra.get("model_id"),
                phase=phase,
                eventType="phase.completed"
                if response.state is OxyState.COMPLETED
                else "phase.failed",
                payload={
                    "status": EngineeringStatus.COMPLETED.value
                    if response.state is OxyState.COMPLETED
                    else EngineeringStatus.FAILED.value,
                    "summary": f"{profile.name} completed the {phase.value} phase."
                    if response.state is OxyState.COMPLETED
                    else f"{profile.name} failed the {phase.value} phase.",
                },
            )
        )
        if response.state is not OxyState.COMPLETED:
            raise RuntimeError(f"role task failed: {role_id}")
        return response

    @staticmethod
    def _producer(response: OxyResponse) -> tuple[str, str]:
        provider_id = response.extra.get("provider_id")
        model_id = response.extra.get("model_id")
        if not provider_id or not model_id:
            raise RuntimeError(
                "routed model response is missing provider/model metadata"
            )
        return str(provider_id), str(model_id)

    def _record_artifact_event(self, artifact: Any, run_id: str) -> None:
        phase = self.ROLE_PHASES[artifact.producer_role]
        self._append_workflow_event(
            WorkflowEvent(
                eventId=generate_uuid(),
                projectId=artifact.project_id,
                taskId=artifact.task_id,
                runId=run_id,
                agentId=artifact.producer_agent,
                role=artifact.producer_role,
                providerId=artifact.provider_id,
                modelId=artifact.model_id,
                phase=phase,
                eventType="artifact.created",
                payload={
                    "status": EngineeringStatus.COMPLETED.value,
                    "summary": f"{artifact.type.value} created.",
                    "artifact": {
                        "id": artifact.id,
                        "type": artifact.type.value,
                        "schemaVersion": artifact.schema_version,
                        "validationStatus": artifact.validation_status.value,
                    },
                },
            )
        )

    async def _execute(self, oxy_request: OxyRequest) -> OxyResponse:
        idea = oxy_request.get_query()
        if not idea.strip():
            raise ValueError("workflow idea must not be empty")
        project_id = oxy_request.arguments.get("project_id") or generate_uuid()
        run_id = oxy_request.current_trace_id or generate_uuid()

        pm_task_id = generate_uuid()
        pm = await self._call_role(
            oxy_request,
            "product_manager",
            "Create a requirement specification for this idea:\n\n" + idea,
            project_id=project_id,
            task_id=pm_task_id,
            run_id=run_id,
        )
        pm_provider, pm_model = self._producer(pm)
        requirement = self.artifact_store.append(
            RequirementSpec(
                project_id=project_id,
                task_id=pm_task_id,
                producer_role="product_manager",
                producer_agent=self.agent_profiles["product_manager"].id,
                provider_id=pm_provider,
                model_id=pm_model,
                content=RequirementSpecContent(summary=str(pm.output)),
                validation_status=ValidationStatus.VALID,
            )
        )
        self._record_artifact_event(requirement, run_id)

        architect_task_id = generate_uuid()
        architect = await self._call_role(
            oxy_request,
            "solution_architect",
            "Create architecture decisions from this RequirementSpec only:\n\n"
            + requirement.model_dump_json(by_alias=True),
            project_id=project_id,
            task_id=architect_task_id,
            run_id=run_id,
            producer_provider_id=pm_provider,
        )
        architect_provider, architect_model = self._producer(architect)
        architecture = self.artifact_store.append(
            ArchitectureDecision(
                project_id=project_id,
                task_id=architect_task_id,
                producer_role="solution_architect",
                producer_agent=self.agent_profiles["solution_architect"].id,
                provider_id=architect_provider,
                model_id=architect_model,
                content=ArchitectureDecisionContent(summary=str(architect.output)),
                source_artifact_ids=[requirement.id],
                validation_status=ValidationStatus.VALID,
            )
        )
        self._record_artifact_event(architecture, run_id)

        lead_task_id = generate_uuid()
        lead = await self._call_role(
            oxy_request,
            "technical_lead",
            "Create an ordered TaskGraph from this ArchitectureDecision only:\n\n"
            + architecture.model_dump_json(by_alias=True),
            project_id=project_id,
            task_id=lead_task_id,
            run_id=run_id,
            producer_provider_id=architect_provider,
        )
        lead_provider, lead_model = self._producer(lead)
        task_graph = self.artifact_store.append(
            TaskGraph(
                project_id=project_id,
                task_id=lead_task_id,
                producer_role="technical_lead",
                producer_agent=self.agent_profiles["technical_lead"].id,
                provider_id=lead_provider,
                model_id=lead_model,
                content=TaskGraphContent(summary=str(lead.output)),
                source_artifact_ids=[architecture.id],
                validation_status=ValidationStatus.VALID,
            )
        )
        self._record_artifact_event(task_graph, run_id)

        review_task_id = generate_uuid()
        reviewer = await self._call_role(
            oxy_request,
            "reviewer",
            "Review this TaskGraph independently and produce a ReviewReport:\n\n"
            + task_graph.model_dump_json(by_alias=True),
            project_id=project_id,
            task_id=review_task_id,
            run_id=run_id,
            producer_provider_id=lead_provider,
        )
        reviewer_provider, reviewer_model = self._producer(reviewer)
        review = self.artifact_store.append(
            ReviewReport(
                project_id=project_id,
                task_id=review_task_id,
                producer_role="reviewer",
                producer_agent=self.agent_profiles["reviewer"].id,
                provider_id=reviewer_provider,
                model_id=reviewer_model,
                content=ReviewReportContent(summary=str(reviewer.output)),
                source_artifact_ids=[task_graph.id],
                validation_status=ValidationStatus.VALID,
            )
        )
        self._record_artifact_event(review, run_id)

        artifacts = [requirement, architecture, task_graph, review]
        return OxyResponse(
            state=OxyState.COMPLETED,
            output={
                "projectId": project_id,
                "runId": run_id,
                "artifacts": [
                    artifact.model_dump(mode="json", by_alias=True)
                    for artifact in artifacts
                ],
            },
            extra={"artifact_ids": [artifact.id for artifact in artifacts]},
        )
