# Real Web workflow integration

The Project UI can now launch the existing `BasicRoleWorkflow` instead of
displaying only seeded timeline data.

```text
Project Idea form
  -> POST /api/v1/platform/projects/{projectId}/workflows/runs
  -> PlatformServices background run
  -> MasWorkflowExecutor
  -> BasicRoleWorkflow
  -> Product Manager / Architect / Technical Lead / Reviewer
  -> ModelRouter and Provider adapters
  -> Artifact, Usage, RouteDecision, WorkflowEvent stores
  -> SSE timeline and Project Artifact views
```

## Safety and compatibility

- Real execution is opt-in through `OXYGENT_ENABLE_REAL_WORKFLOW=1`.
- Credentials remain environment references and are not serialized to Profiles,
  workflow events, API responses, or ordinary logs.
- The existing Chat master Agent remains present. The role workflow is invoked
  by explicit callee name and does not replace general Chat.
- Runs execute in the background and are serialized on the first MAS runtime to
  avoid shared request-state races.
- SSE responses use the existing safe workflow event view, which excludes
  prompts, raw model output, credentials, and private reasoning.
- Stores remain in memory; restarting the service clears newly generated data.

## PyCharm configuration

Use `examples/platform/projects_web_demo.py` as the script, the repository root
as the working directory, and `.conda-env/bin/python` as the interpreter. Copy
the variable names from `examples/platform/.env.multi_role.example` into the
Run Configuration environment editor and replace only the local values there.
Do not commit a `.env` containing credentials.

After startup, open `/web/projects.html`, enter a Project, choose **Ideas**, and
start the workflow. The browser redirects to `/web/workflows.html?runId=...`
and updates the timeline from the SSE stream.
