"""Deterministic, credential-safe usage and cost aggregation."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Iterable

from pydantic import Field, model_validator

from .common import PlatformModel, utc_now
from .projects import Project
from .usage import InvocationStatus, ModelUsage


class InsightDimension(str, Enum):
    PROJECT = "project"
    ROLE = "role"
    PROVIDER = "provider"
    MODEL = "model"
    WORKFLOW = "workflow"
    STATUS = "status"
    DAY = "day"


class BudgetStatus(str, Enum):
    UNCONFIGURED = "unconfigured"
    ON_TRACK = "onTrack"
    WARNING = "warning"
    EXCEEDED = "exceeded"


class InsightsQuery(PlatformModel):
    project_id: str | None = None
    role_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None
    run_id: str | None = None
    status: InvocationStatus | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "InsightsQuery":
        for value in (self.date_from, self.date_to):
            if value is not None and value.tzinfo is None:
                raise ValueError("Insights dates must include a timezone")
        if self.date_from and self.date_to and self.date_from >= self.date_to:
            raise ValueError("dateFrom must be earlier than dateTo")
        return self


class InsightTotals(PlatformModel):
    invocations: int = Field(default=0, ge=0)
    succeeded: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    fallback_invocations: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0)
    priced_invocations: int = Field(default=0, ge=0)
    unpriced_invocations: int = Field(default=0, ge=0)
    average_latency_ms: float = Field(default=0.0, ge=0)
    p95_latency_ms: float = Field(default=0.0, ge=0)
    success_rate: float | None = Field(default=None, ge=0, le=1)
    fallback_rate: float | None = Field(default=None, ge=0, le=1)
    cost_coverage: float | None = Field(default=None, ge=0, le=1)


class InsightBreakdownRow(PlatformModel):
    dimension: InsightDimension
    key: str
    label: str
    totals: InsightTotals


class BudgetSnapshot(PlatformModel):
    project_id: str
    project_name: str
    monthly_budget: float | None = Field(default=None, ge=0)
    current_spend: float = Field(default=0.0, ge=0)
    percent_used: float | None = Field(default=None, ge=0)
    status: BudgetStatus = BudgetStatus.UNCONFIGURED
    priced_invocations: int = Field(default=0, ge=0)
    unpriced_invocations: int = Field(default=0, ge=0)
    period_start: datetime
    calculated_at: datetime = Field(default_factory=utc_now)


def filter_usage(
    records: Iterable[ModelUsage], query: InsightsQuery
) -> list[ModelUsage]:
    """Apply an inclusive start and exclusive end range."""
    values = []
    for record in records:
        created_at = _as_utc(record.created_at)
        if query.project_id is not None and record.project_id != query.project_id:
            continue
        if query.role_id is not None and record.role_id != query.role_id:
            continue
        if query.provider_id is not None and record.provider_id != query.provider_id:
            continue
        if query.model_id is not None and record.model_id != query.model_id:
            continue
        if query.run_id is not None and record.run_id != query.run_id:
            continue
        if query.status is not None and record.status is not query.status:
            continue
        if query.date_from is not None and created_at < _as_utc(query.date_from):
            continue
        if query.date_to is not None and created_at >= _as_utc(query.date_to):
            continue
        values.append(record)
    return sorted(values, key=lambda item: _as_utc(item.created_at), reverse=True)


def aggregate_usage(records: Iterable[ModelUsage]) -> InsightTotals:
    values = list(records)
    invocations = len(values)
    succeeded = sum(item.status is InvocationStatus.SUCCEEDED for item in values)
    fallback = sum(item.fallback_used for item in values)
    priced = [item for item in values if item.cost_available]
    latencies = sorted(item.latency_ms for item in values)
    p95_index = max(0, math.ceil(len(latencies) * 0.95) - 1)
    return InsightTotals(
        invocations=invocations,
        succeeded=succeeded,
        failed=invocations - succeeded,
        fallbackInvocations=fallback,
        inputTokens=sum(item.input_tokens for item in values),
        outputTokens=sum(item.output_tokens for item in values),
        totalTokens=sum(item.input_tokens + item.output_tokens for item in values),
        estimatedCost=sum(item.estimated_cost for item in priced),
        pricedInvocations=len(priced),
        unpricedInvocations=invocations - len(priced),
        averageLatencyMs=(
            sum(item.latency_ms for item in values) / invocations if invocations else 0
        ),
        p95LatencyMs=latencies[p95_index] if latencies else 0,
        successRate=succeeded / invocations if invocations else None,
        fallbackRate=fallback / invocations if invocations else None,
        costCoverage=len(priced) / invocations if invocations else None,
    )


def breakdown_usage(
    records: Iterable[ModelUsage],
    dimension: InsightDimension,
    label_resolver: Callable[[InsightDimension, str], str] | None = None,
) -> list[InsightBreakdownRow]:
    groups: dict[str, list[ModelUsage]] = defaultdict(list)
    for record in records:
        groups[_dimension_key(record, dimension)].append(record)
    rows = [
        InsightBreakdownRow(
            dimension=dimension,
            key=key,
            label=(label_resolver(dimension, key) if label_resolver else key),
            totals=aggregate_usage(group),
        )
        for key, group in groups.items()
    ]
    return sorted(
        rows,
        key=lambda row: (
            -row.totals.estimated_cost,
            -row.totals.invocations,
            row.label.lower(),
        ),
    )


def build_budget_snapshots(
    projects: Iterable[Project],
    records: Iterable[ModelUsage],
    *,
    now: datetime | None = None,
) -> list[BudgetSnapshot]:
    calculated_at = _as_utc(now or utc_now())
    period_start = calculated_at.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    monthly_records = [
        record for record in records if _as_utc(record.created_at) >= period_start
    ]
    snapshots = []
    for project in projects:
        project_records = [
            record for record in monthly_records if record.project_id == project.id
        ]
        totals = aggregate_usage(project_records)
        budget = _monthly_budget(project)
        percent_used = totals.estimated_cost / budget if budget else None
        if budget is None:
            budget_status = BudgetStatus.UNCONFIGURED
        elif totals.estimated_cost >= budget:
            budget_status = BudgetStatus.EXCEEDED
        elif percent_used is not None and percent_used >= 0.8:
            budget_status = BudgetStatus.WARNING
        else:
            budget_status = BudgetStatus.ON_TRACK
        snapshots.append(
            BudgetSnapshot(
                projectId=project.id,
                projectName=project.name,
                monthlyBudget=budget,
                currentSpend=totals.estimated_cost,
                percentUsed=percent_used,
                status=budget_status,
                pricedInvocations=totals.priced_invocations,
                unpricedInvocations=totals.unpriced_invocations,
                periodStart=period_start,
                calculatedAt=calculated_at,
            )
        )
    order = {
        BudgetStatus.EXCEEDED: 0,
        BudgetStatus.WARNING: 1,
        BudgetStatus.ON_TRACK: 2,
        BudgetStatus.UNCONFIGURED: 3,
    }
    return sorted(snapshots, key=lambda item: (order[item.status], item.project_name))


def _dimension_key(record: ModelUsage, dimension: InsightDimension) -> str:
    if dimension is InsightDimension.PROJECT:
        value = record.project_id
    elif dimension is InsightDimension.ROLE:
        value = record.role_id
    elif dimension is InsightDimension.PROVIDER:
        value = record.provider_id
    elif dimension is InsightDimension.MODEL:
        value = record.model_id
    elif dimension is InsightDimension.WORKFLOW:
        value = record.run_id
    elif dimension is InsightDimension.STATUS:
        value = record.status.value
    else:
        value = _as_utc(record.created_at).date().isoformat()
    return value or "unassigned"


def _monthly_budget(project: Project) -> float | None:
    value = project.settings.get("monthlyBudget")
    if isinstance(value, bool):
        return None
    try:
        budget = float(value)
    except (TypeError, ValueError):
        return None
    return budget if math.isfinite(budget) and budget > 0 else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
