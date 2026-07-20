"""Immutable, schema-validated artifacts for role-to-role collaboration."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import ConfigDict, Field

from oxygent.utils.common_utils import generate_uuid

from .common import PlatformModel, utc_now


class ArtifactType(str, Enum):
    REQUIREMENT_SPEC = "RequirementSpec"
    ARCHITECTURE_DECISION = "ArchitectureDecision"
    TASK_GRAPH = "TaskGraph"
    REVIEW_REPORT = "ReviewReport"


class ValidationStatus(str, Enum):
    UNVALIDATED = "unvalidated"
    VALID = "valid"
    INVALID = "invalid"


class RequirementSpecContent(PlatformModel):
    summary: str = Field(min_length=1)
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)


class ArchitectureDecisionContent(PlatformModel):
    summary: str = Field(min_length=1)
    decisions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)


class TaskNode(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    title: str = Field(min_length=1)
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)


class TaskGraphContent(PlatformModel):
    summary: str = Field(min_length=1)
    tasks: list[TaskNode] = Field(default_factory=list)


class ReviewFinding(PlatformModel):
    severity: str = "info"
    message: str = Field(min_length=1)
    source_artifact_id: str = ""


class ReviewReportContent(PlatformModel):
    summary: str = Field(min_length=1)
    approved: bool | None = None
    findings: list[ReviewFinding] = Field(default_factory=list)


class ArtifactBase(PlatformModel):
    """Common immutable metadata for every artifact revision."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=generate_uuid)
    type: ArtifactType
    schema_version: str = "1.0"
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    producer_role: str = Field(min_length=1)
    producer_agent: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    content: Any
    source_artifact_ids: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = ValidationStatus.UNVALIDATED
    revision: int = Field(default=1, ge=1)
    supersedes_artifact_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class RequirementSpec(ArtifactBase):
    type: Literal[ArtifactType.REQUIREMENT_SPEC] = ArtifactType.REQUIREMENT_SPEC
    content: RequirementSpecContent


class ArchitectureDecision(ArtifactBase):
    type: Literal[ArtifactType.ARCHITECTURE_DECISION] = (
        ArtifactType.ARCHITECTURE_DECISION
    )
    content: ArchitectureDecisionContent


class TaskGraph(ArtifactBase):
    type: Literal[ArtifactType.TASK_GRAPH] = ArtifactType.TASK_GRAPH
    content: TaskGraphContent


class ReviewReport(ArtifactBase):
    type: Literal[ArtifactType.REVIEW_REPORT] = ArtifactType.REVIEW_REPORT
    content: ReviewReportContent


Artifact: TypeAlias = Annotated[
    RequirementSpec | ArchitectureDecision | TaskGraph | ReviewReport,
    Field(discriminator="type"),
]


class InMemoryArtifactStore:
    """Append-only Artifact store. Existing IDs can never be overwritten."""

    def __init__(self) -> None:
        self._artifacts: dict[str, ArtifactBase] = {}

    def append(self, artifact: ArtifactBase) -> ArtifactBase:
        if artifact.id in self._artifacts:
            raise ValueError(f"artifact already exists: {artifact.id}")
        self._artifacts[artifact.id] = artifact
        return artifact

    def get(self, artifact_id: str) -> ArtifactBase:
        try:
            return self._artifacts[artifact_id]
        except KeyError as exc:
            raise KeyError(f"artifact not found: {artifact_id}") from exc

    def list(self, project_id: str | None = None) -> list[ArtifactBase]:
        values = list(self._artifacts.values())
        if project_id is None:
            return values
        return [artifact for artifact in values if artifact.project_id == project_id]

    def revise(
        self,
        artifact_id: str,
        content: PlatformModel | dict[str, Any],
        *,
        producer_role: str | None = None,
        producer_agent: str | None = None,
        provider_id: str | None = None,
        model_id: str | None = None,
        validation_status: ValidationStatus = ValidationStatus.UNVALIDATED,
    ) -> ArtifactBase:
        previous = self.get(artifact_id)
        data = previous.model_dump()
        data.update(
            {
                "id": generate_uuid(),
                "content": content,
                "producer_role": producer_role or previous.producer_role,
                "producer_agent": producer_agent or previous.producer_agent,
                "provider_id": provider_id or previous.provider_id,
                "model_id": model_id or previous.model_id,
                "source_artifact_ids": [
                    *previous.source_artifact_ids,
                    previous.id,
                ],
                "validation_status": validation_status,
                "revision": previous.revision + 1,
                "supersedes_artifact_id": previous.id,
                "created_at": utc_now(),
            }
        )
        revision = previous.__class__.model_validate(data)
        return self.append(revision)
