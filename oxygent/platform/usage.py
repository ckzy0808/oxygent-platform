"""Append-only model invocation usage records."""

from enum import Enum
from datetime import datetime
from typing import Optional

from pydantic import Field

from .common import PlatformModel, utc_now


class InvocationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ModelUsage(PlatformModel):
    project_id: str
    task_id: str
    run_id: str
    role_id: str
    agent_id: str
    provider_id: str
    model_id: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0)
    cost_available: bool = True
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

    def list(self) -> list[ModelUsage]:
        return list(self._records)

    def historical_success_rate(self, model_id: str) -> float:
        records = [record for record in self._records if record.model_id == model_id]
        if not records:
            return 0.5
        succeeded = sum(
            record.status is InvocationStatus.SUCCEEDED for record in records
        )
        return succeeded / len(records)
