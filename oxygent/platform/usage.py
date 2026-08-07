"""Append-only model invocation usage records."""

from __future__ import annotations

from enum import Enum
from datetime import datetime
from typing import Optional

from pydantic import Field

from oxygent.schemas.usage import EstimationMethod
from oxygent.utils.common_utils import generate_uuid

from .common import PlatformModel, utc_now


class InvocationStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ModelUsage(PlatformModel):
    id: str = Field(default_factory=generate_uuid)
    project_id: str
    task_id: str
    run_id: str
    role_id: str
    agent_id: str
    provider_id: str
    model_id: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    token_count_method: EstimationMethod = EstimationMethod.EXACT
    invocation_type: str = "model"
    latency_ms: float = Field(default=0.0, ge=0)
    # Legacy routing-cost inputs remain accepted, but dollar values are not exposed
    # or persisted by product-facing serialization.
    estimated_cost: float = Field(default=0.0, ge=0, exclude=True)
    cost_available: bool = Field(default=True, exclude=True)
    status: InvocationStatus
    failure_reason: Optional[str] = None
    fallback_used: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class InMemoryModelUsageStore:
    """Append-only usage store with routing statistics helpers."""

    def __init__(self) -> None:
        self._records: list[ModelUsage] = []

    def append(self, usage: ModelUsage) -> None:
        self._records.append(usage)

    def upsert(self, usage: ModelUsage) -> None:
        """Replace one live invocation record or append it if it is new."""
        for index, record in enumerate(self._records):
            if record.id == usage.id:
                self._records[index] = usage
                return
        self._records.append(usage)

    def list(self) -> list[ModelUsage]:
        return list(self._records)

    def for_run(self, run_id: str) -> list[ModelUsage]:
        return [record for record in self._records if record.run_id == run_id]

    def historical_success_rate(self, model_id: str) -> float:
        records = [
            record
            for record in self._records
            if record.model_id == model_id
            and record.status is not InvocationStatus.RUNNING
        ]
        if not records:
            return 0.5
        succeeded = sum(
            record.status is InvocationStatus.SUCCEEDED for record in records
        )
        return succeeded / len(records)
