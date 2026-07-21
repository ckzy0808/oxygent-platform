"""Deterministic Insights aggregation tests."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from oxygent.platform import (
    BudgetStatus,
    InsightDimension,
    InsightsQuery,
    InvocationStatus,
    ModelUsage,
    Project,
    aggregate_usage,
    breakdown_usage,
    build_budget_snapshots,
    filter_usage,
)


NOW = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)


def usage(
    *,
    project: str = "project-a",
    model: str = "model-a",
    status: InvocationStatus = InvocationStatus.SUCCEEDED,
    cost: float = 0.02,
    cost_available: bool = True,
    created_at: datetime = NOW,
    fallback: bool = False,
) -> ModelUsage:
    return ModelUsage(
        projectId=project,
        taskId="task-a",
        runId="run-a",
        roleId="product_manager",
        agentId="pm-agent",
        providerId="provider-a",
        modelId=model,
        inputTokens=100,
        outputTokens=40,
        latencyMs=250,
        estimatedCost=cost,
        costAvailable=cost_available,
        status=status,
        fallbackUsed=fallback,
        createdAt=created_at,
    )


def test_usage_totals_track_cost_coverage_reliability_and_fallback():
    totals = aggregate_usage(
        [
            usage(),
            usage(
                model="model-b",
                status=InvocationStatus.FAILED,
                cost=0,
                cost_available=False,
                fallback=True,
            ),
        ]
    )

    assert totals.invocations == 2
    assert totals.total_tokens == 280
    assert totals.estimated_cost == pytest.approx(0.02)
    assert totals.priced_invocations == 1
    assert totals.unpriced_invocations == 1
    assert totals.cost_coverage == pytest.approx(0.5)
    assert totals.success_rate == pytest.approx(0.5)
    assert totals.fallback_rate == pytest.approx(0.5)
    assert totals.p95_latency_ms == 250


def test_filters_use_inclusive_start_and_exclusive_end():
    records = [
        usage(created_at=NOW - timedelta(days=1)),
        usage(model="model-b", created_at=NOW),
        usage(model="model-c", created_at=NOW + timedelta(days=1)),
    ]
    query = InsightsQuery(dateFrom=NOW, dateTo=NOW + timedelta(days=1))
    assert [record.model_id for record in filter_usage(records, query)] == ["model-b"]


def test_naive_filter_dates_are_rejected():
    with pytest.raises(ValidationError, match="timezone"):
        InsightsQuery(dateFrom=datetime(2026, 7, 1))


def test_breakdown_keeps_historical_unassigned_records_visible():
    rows = breakdown_usage(
        [usage(project=""), usage(project="project-a")],
        InsightDimension.PROJECT,
        lambda _dimension, key: "Unassigned" if key == "unassigned" else key,
    )
    assert {row.key for row in rows} == {"unassigned", "project-a"}
    assert next(row.label for row in rows if row.key == "unassigned") == "Unassigned"


def test_monthly_budget_status_uses_priced_cost_without_hiding_unknown_costs():
    project = Project(
        id="project-a",
        name="Project A",
        settings={"monthlyBudget": 0.02},
    )
    snapshot = build_budget_snapshots(
        [project],
        [usage(cost=0.018), usage(cost=0, cost_available=False)],
        now=NOW,
    )[0]

    assert snapshot.status is BudgetStatus.WARNING
    assert snapshot.percent_used == pytest.approx(0.9)
    assert snapshot.priced_invocations == 1
    assert snapshot.unpriced_invocations == 1


def test_invalid_or_missing_budget_is_unconfigured():
    project = Project(id="project-a", name="Project A", settings={"monthlyBudget": 0})
    snapshot = build_budget_snapshots([project], [usage()], now=NOW)[0]
    assert snapshot.status is BudgetStatus.UNCONFIGURED
    assert snapshot.monthly_budget is None
