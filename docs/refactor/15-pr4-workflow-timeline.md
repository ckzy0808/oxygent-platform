# PR 4: Workflow Timeline

## Outcome

PR 4 replaces the Workflows foundation placeholder with a live, structured
Timeline. It projects append-only execution events into seven engineering
phases and keeps detailed model/tool activity in an advanced Execution Drawer.

This PR is additive to PR 1–3. Existing Chat/SSE, MAS, ReActAgent, Tools, MCP,
Projects, Artifacts, Agents, Models, CLI, and batch behavior are unchanged.
The formal `demo.py` startup is deliberately not modified by this PR.

## Unified event contract

`WorkflowEvent` uses the product event fields defined in the target
architecture:

```text
eventId
projectId
taskId
runId
agentId
role
providerId
modelId
phase
eventType
timestamp
payload
```

Events are append-only. Duplicate `eventId` values are rejected. The in-memory
store returns copies and supports filtering by Project, Task, and Run.

The first projection supports:

```text
Requirement
Architecture
Plan
Implementation
Verification
Review
Approval
```

The status vocabulary is:

```text
Not started
Analyzing
Planning
Implementing
Testing
Reviewing
Awaiting approval
Completed
Blocked
Failed
```

## Runtime compatibility

`BasicRoleWorkflow` still writes its existing `ExecutionTrace` records. It now
also emits product-safe `WorkflowEvent` records for role phase start,
completion/failure, and Artifact creation. This is an additive bridge rather
than a rewrite of the workflow or trace system.

The existing four-role Artifact workflow currently covers Requirement,
Architecture, Plan, and Review. Implementation, Verification, and Approval are
available in the event model and projection for later Code Workspace work.

## Product APIs

```text
GET /api/v1/platform/workflows/runs
GET /api/v1/platform/workflows/runs/{runId}
GET /api/v1/platform/workflows/runs/{runId}/events
```

The list endpoint accepts optional `projectId` and `taskId` filters. Run
responses contain the seven projected stages, current phase, status, total
cost, total duration, role, Agent, Provider, Model, tools, and Artifact
references.

## Event safety

The event endpoint is a product projection, not a raw log endpoint. It exposes
only an allow-list of metadata fields:

- status and summaries;
- tool names and recorded exit codes;
- Artifact identity and validation metadata;
- duration and estimated cost.

Prompts, raw model output, credential values, authorization material, private
reasoning, arbitrary payload keys, and Artifact content are excluded. Bearer
tokens and common authorization assignments in allowed text fields are
redacted.

## UI

The Workflows page now provides:

- Workflow Run selection;
- seven-phase status rail;
- engineering-stage Timeline cards;
- explicit Role, Agent, Provider, and Model attribution;
- summary, tools, Artifact, cost, duration, and event count;
- selected-phase detail panel;
- advanced Execution Drawer with sanitized event metadata.

The main interface does not present execution as Agent group chat and does not
show private model reasoning.

## Run locally

PR 4 extends the credential-free product demo:

```bash
cd /Users/zeyucheng/Documents/oxyAgent
OXYGENT_PROJECT_DEMO_PORT=18082 PYTHONPATH=. \
  .conda-env/bin/python examples/platform/projects_web_demo.py
```

Open:

```text
http://127.0.0.1:18082/web/workflows.html
```

The Workflow Timeline demo data exists only in `examples/`. It is not embedded
in domain models or frontend code.

## Migration and rollback

No database migration is required. Workflow events use the existing caller-
owned in-memory trace container. Persistent event storage, live event streaming,
workflow definitions, Task DAG editing, retries, and approval mutations remain
future work.

Removing `workflow-page.js`, its stylesheet, and the three Workflow API routes
returns the Workflows page to the PR 1 foundation without affecting existing
runtime behavior.

## Browser verification

The PR 4 demo was verified at `http://127.0.0.1:18082`. Verification covered
all seven phases, current approval status, Role/Agent/Provider/Model attribution,
phase selection, the 13-event Execution Drawer, event safety, an empty browser
error console, and PR 1–3 page regressions.

- `screenshots/pr4-workflow-timeline.png`
- `screenshots/pr4-execution-drawer.png`

## Verification results

- Unit suite: `370 passed, 13 skipped`;
- PR 2–4 platform and workflow integration tests: `11 passed`;
- Browser: seven phases, drawer interaction, event safety, PR 1–3 regression,
  and zero console errors;
- Ruff, Ruff format, JavaScript syntax, diff whitespace, and known credential
  scans pass.

The complete legacy integration directory reports `270 passed` and three
environment-dependent failures: one test requires the external `uvx` command,
and two legacy flow-image tests require `OPENAI_*` variables rather than this
workspace's configured `DEFAULT_LLM_*` variables. These failures do not execute
or import PR 4 code.
