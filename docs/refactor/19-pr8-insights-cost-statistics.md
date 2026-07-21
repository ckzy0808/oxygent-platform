# PR 8 — Insights and Cost Statistics

## Outcome

PR 8 replaces the Insights placeholder with a dynamic, read-only operational
dashboard backed by the existing append-only `ModelUsage` and execution trace
stores. It reports usage, estimated cost, latency, reliability, fallback rate,
budget status, and safe run links without changing model routing or exposing
private reasoning.

Chat, Projects, Code Workspace, MAS, ReActAgent, Tool, MCP, CLI, batch, SSE, and
the PR 1–7 product capabilities remain additive and unchanged.

## Aggregation model

`oxygent/platform/insights.py` adds deterministic aggregation functions and
Pydantic response schemas:

- `InsightsQuery` — Project, role, Provider, model, Workflow run, status, and
  timezone-aware date filters;
- `InsightTotals` — calls, tokens, estimated cost, price coverage, average/P95
  latency, success rate, and fallback rate;
- `InsightBreakdownRow` — Project, role, Provider, model, Workflow, status, or
  UTC day grouping;
- `BudgetSnapshot` — current UTC month spend compared with the Project setting
  `monthlyBudget`;
- `filter_usage`, `aggregate_usage`, `breakdown_usage`, and
  `build_budget_snapshots` — side-effect-free functions reusable by another
  storage adapter.

Date ranges use an inclusive `dateFrom` and exclusive `dateTo`. Naive datetimes
are rejected so deployments cannot silently mix local and UTC boundaries.
Historical records with blank grouping IDs are retained under `Unassigned`.

## Cost semantics

`ModelUsage.costAvailable` defaults to `true` for backward compatibility. A call
whose model price is unavailable sets it to `false`; its tokens, latency,
reliability, and invocation count remain visible, while its numeric value is
excluded from estimated-cost totals. Responses show priced/unpriced invocation
counts and price coverage.

Costs are estimates recorded at invocation time. They are not Provider invoices
and the dashboard labels them accordingly.

Project budget status is observational:

- below 80%: `onTrack`;
- 80% to below 100%: `warning`;
- at or above 100%: `exceeded`;
- absent or invalid budget: `unconfigured`.

Insights never mutates Role Model Policy or blocks a call.

## API additions

All routes are below `/api/v1/platform`:

- `GET /insights/summary`
- `GET /insights/breakdown?dimension=project|role|provider|model|workflow|status|day`
- `GET /insights/runs`

Common filters are `projectId`, `roleId`, `providerId`, `modelId`, `runId`,
`status`, `dateFrom`, and `dateTo`. The run endpoint also accepts a bounded
`limit` from 1 to 100.

Project filters are validated against the Project repository. Breakdown labels
are resolved from the current registries when possible. Failure details become
the generic `Provider call failed`, route reasons pass through credential
redaction, and links contain URL-encoded opaque IDs only. Prompts, model output,
tool output, credentials, and private reasoning are not returned.

## UI

`web/insights.html` now loads a dedicated script and stylesheet. It provides:

- Overview, Usage, Cost, and Reliability tabs;
- Project and time-range filters;
- invocation, token, estimated-cost, success, and P95-latency cards;
- Project budget cards with explicit unpriced-call notices;
- dimension-specific breakdown tables;
- recent run links with policy-level routing reasons.

The view keeps the existing OxyGent navigation and visual tokens. It uses a
compact responsive table rather than exposing raw execution logs.

Browser verification capture: `docs/refactor/screenshots/pr8-insights.png`.

## Demo

No repository or credential is needed:

```bash
OXYGENT_PROJECT_DEMO_PORT=18086 \
PYTHONPATH=. .conda-env/bin/python examples/platform/projects_web_demo.py
```

Open `http://127.0.0.1:18086/web/insights.html`. The demo contains four local
usage records and a generic Project monthly budget. No Provider request occurs.

## Migration and rollback

Existing `ModelUsage` construction remains valid because `costAvailable`
defaults to `true`. Existing Projects without `settings.monthlyBudget` appear as
`unconfigured`. The initial aggregation reads the current in-memory stores and
does not require a persistence migration.

Rollback removes this additive API/UI commit. PR 7 approvals and all earlier
pages continue to run. No stored model invocation or Git state is changed by
Insights reads.

## Verification and current limits

Automated tests cover aggregation, exclusive date boundaries, timezone
validation, Project isolation, Registry labels, unassigned history, missing
price coverage, budget states, credential/failure redaction, API links, static
page contracts, and earlier control-plane behavior.

Not yet implemented:

- persistent analytics storage and retention policy;
- Provider billing reconciliation or a versioned pricing catalog;
- alert delivery and budget enforcement;
- percentile aggregation optimized for very large datasets;
- cross-process aggregation over Redis or Elasticsearch;
- invoice-grade currency, tax, and exchange-rate handling.
