# PR 7 — Approval, Apply, Export, and Discard

## Outcome

PR 7 adds an auditable approval lifecycle to the isolated Code Task introduced
in PR 5 and verified in PR 6. Approval remains a review decision: it performs no
Git mutation. Apply is a separate action that commits the exact approved and
verified content only to the task worktree branch.

The implementation does not merge into the source branch, push a remote, write
through an Agent, or change the existing Chat, MAS, Tool, MCP, CLI, and batch
paths.

## State and audit model

`CodeTask.approvalState` progresses through:

```text
draft -> awaitingApproval -> approved -> applied
             |                  |
             +-> revisionRequested
             +-> discarded
```

Every user-visible action appends a frozen `ApprovalRecord` with actor, action,
reason, diff content hash, matching verification run IDs, and an optional commit
or recovery-patch reference. Records are append-only in the initial in-memory
store and are exposed as an audit history.

The system never stores an API key, model prompt, private reasoning, or raw
credential in an approval record.

## Approval and Apply invariants

- `Approve changes` captures and hashes the current bounded Diff. It does not
  invoke Git.
- High-risk Change Contracts require a human actor.
- `Apply to branch` requires the task to be approved.
- The current Diff hash must exactly match the approved hash.
- At least one passed Verification Run must reference that same hash.
- Scope Guard runs again immediately before both approval and apply.
- Truncated and empty Diffs cannot be approved.
- Apply uses fixed Git argument arrays, creates one commit on the task branch,
  and records its commit ID.
- Apply never merges, checks out, resets, pushes, or mutates the source branch.

## Revision, patch export, and discard

`Request revision` clears the approved hash and returns the task to an editable
review state. `Export patch` creates an immutable Recovery Patch from the task's
base commit and returns it only through a task-scoped endpoint.

`Discard` requires the exact confirmation value `DISCARD`. Before removing the
worktree and task branch, the service stores a Recovery Patch. The Code Task
record remains available with `discarded` state and the patch reference, while
all later mutating approval actions are rejected.

## API additions

All routes remain below `/api/v1/platform` and reuse the loopback-or-auth
boundary:

- `GET /projects/{projectId}/code-tasks/{taskId}/approvals`
- `POST /projects/{projectId}/code-tasks/{taskId}/request-revision`
- `POST /projects/{projectId}/code-tasks/{taskId}/approve`
- `POST /projects/{projectId}/code-tasks/{taskId}/apply`
- `POST /projects/{projectId}/code-tasks/{taskId}/export-patch`
- `POST /projects/{projectId}/code-tasks/{taskId}/discard`
- `GET /projects/{projectId}/code-tasks/{taskId}/recovery-patches/{patchId}`

Patch content is omitted from ordinary action responses. It is returned only by
the explicit recovery-patch endpoint.

## UI behavior

The Code Workspace Review tab presents `Approve changes` and `Apply to branch`
as numbered, separate operations. It also provides Request revision, Export
patch, and Discard controls and shows the immutable audit trail. The interface
states that Apply commits only to the isolated task branch.

Discard uses a browser confirmation followed by the server-side exact-value
check. Export downloads a `.patch` file without injecting the patch body into
the regular activity timeline.

## Demo

Use an explicitly approved disposable Git repository:

```bash
OXYGENT_PROJECT_DEMO_PORT=18085 \
OXYGENT_DEMO_REPOSITORY=/absolute/path/to/a/disposable/git/repository \
OXYGENT_CODE_WORKSPACE_ROOT=/absolute/path/to/a/worktree/root \
OXYGENT_DEMO_SEED_CODE_TASK=1 \
OXYGENT_DEMO_SEED_DIFF=1 \
OXYGENT_DEMO_SEED_APPROVAL=1 \
PYTHONPATH=. .conda-env/bin/python examples/platform/projects_web_demo.py
```

Open `http://127.0.0.1:18085/web/code.html`. The approval seed performs no Git
commit. Use Apply explicitly to create a commit on the disposable task branch.

## Migration and rollback

Existing Code Tasks default to `draft`; the new fields are optional or have safe
defaults. Applications without Code Workspace configuration cannot invoke the
lifecycle. No persistent schema migration is required for the in-memory store.

Rollback removes this additive module/API/UI commit. Existing worktrees and PR 6
verification records remain usable; commits already created on isolated task
branches remain ordinary Git objects and are not automatically removed.

## Verification evidence

Automated tests cover audit immutability, approval without Git mutation, stale
Diff rejection, missing/stale verification rejection, task-branch-only commit,
human approval for high risk, revision, recovery-patch export, confirmed
discard, API isolation, and legacy Code Workspace regressions.
