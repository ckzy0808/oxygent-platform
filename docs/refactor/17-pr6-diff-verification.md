# PR 6 — Diff and Verification

## Outcome

PR 6 extends the isolated Code Task from PR 5 with bounded diff inspection and
server-controlled verification. It does not add Agent file writes, approval,
apply, discard, merge, commit, push, or remote mutation.

## New backend module

`oxygent/platform/verification.py` provides:

- `DiffSnapshot` with base commit, changed files, additions, deletions, changed
  line count, truncation, and Scope Guard status;
- `VerificationProfile` and typed slots for format, lint, typecheck, compile,
  unit, integration, and build;
- `VerificationCommand`, represented exclusively as an argv array;
- `VerificationRunner`, `VerificationRun`, status, command-definition hash,
  preview output, and immutable output references;
- concurrency-safe in-memory profile, result, and output stores.

Stores and the runner remain explicit `PlatformServices` dependencies and can be
replaced without changing the Project, Agent, or MAS core.

## Diff behavior

`capture_diff()` compares the Code Task worktree with its recorded base commit.
It includes tracked modifications and untracked, non-ignored files. Git is
invoked with fixed argument arrays and `--no-ext-diff`, `--no-color`, and bounded
output. The response records real additions and deletions.

The service executes `ScopeGuard.check_diff()` before returning content. When a
path, dependency policy, changed-file limit, or diff-line limit fails, the API
returns changed-file metadata and a `blocked:` status but withholds the diff
body. The same check runs again immediately before every verification command.

## Verification security

- Commands are immutable argument arrays. No endpoint or runner accepts a Shell
  command string and no subprocess uses `shell=True`.
- Shell interpreters such as `sh`, `bash`, `zsh`, PowerShell, and `cmd` are
  rejected as command executables.
- The host application supplies an explicit executable allow-list. A Project
  profile cannot run an executable outside that list.
- Shell metacharacters in later argv entries remain literal process arguments.
- Working directories are repository-relative, canonicalized, and constrained
  to the task worktree.
- The environment begins from a host-owned map and exposes only `PATH`, locale,
  and command-approved variable names. Browser and model requests never supply
  environment values.
- Timeout kills the isolated process group. stdout and stderr are drained with
  memory bounds and truncation is recorded.
- Status uses the real process return code. A model cannot declare a check
  passed.
- Full bounded stdout/stderr are addressable through output IDs; list responses
  include only previews.

## API additions

- `GET /projects/{projectId}/code-tasks/{taskId}/diff`
- `GET /projects/{projectId}/verification-profiles`
- `POST /projects/{projectId}/verification-profiles`
- `GET /projects/{projectId}/code-tasks/{taskId}/verification-runs`
- `POST /projects/{projectId}/code-tasks/{taskId}/verification-runs`
- `GET /projects/{projectId}/code-tasks/{taskId}/verification-outputs/{outputId}`

All paths are below `/api/v1/platform`. They reuse PR 5's loopback-or-auth
boundary. Capability `diffVerification` follows Code Workspace configuration.

## UI behavior

The right side of Code Workspace now has focused tabs:

- Summary — Change Contract and hard limits;
- Changes — actual changed files and additions/deletions;
- Diff — escaped, bounded unified diff or a Scope Guard blocked state;
- Verification — exact argv, recorded status, real exit code, duration, and
  output preview;
- Review — a clear pre-review state without private model reasoning;
- Artifacts — output and Workflow Artifact guidance.

Diff text is HTML escaped before insertion. The UI does not use model claims as
verification status and does not default to raw execution logs.

## Demo

Use a disposable or explicitly approved local Git repository:

```bash
OXYGENT_PROJECT_DEMO_PORT=18084 \
OXYGENT_DEMO_REPOSITORY=/absolute/path/to/an/approved/git/repository \
OXYGENT_CODE_WORKSPACE_ROOT=/absolute/path/to/a/worktree/root \
OXYGENT_DEMO_SEED_CODE_TASK=1 \
OXYGENT_DEMO_SEED_DIFF=1 \
PYTHONPATH=. .conda-env/bin/python examples/platform/projects_web_demo.py
```

Open `http://127.0.0.1:18084/web/code.html`. Demo diff and commands exist only
when the explicit seed flags are set and modify only the disposable task
worktree.

## Verification evidence

Automated coverage includes:

- tracked and untracked unified diff capture;
- additions, deletions, and changed-file counts;
- content withholding on Scope Guard failure;
- exact argv and command-definition hashes;
- shell-interpreter rejection and literal shell-metacharacter arguments;
- real nonzero exit codes;
- timeout and process-group cancellation;
- output limits and immutable output retrieval;
- pre-execution Scope Guard blocking;
- escaped diff renderer contract;
- PR 5 repository isolation and legacy product shell regressions.

Manual browser verification used a disposable clone. It confirmed the six tabs,
escaped three-line untracked-file diff, fixed Python argv, a real exit code of
zero, measured duration, captured output, and the unchanged-source warning.

## Migration and rollback

Existing Projects have no Verification Profile. The feature is inert until the
application explicitly configures Code Workspace, approves executables, and a
Project registers a profile. Rollback removes this additive module/API/UI commit;
PR 5 repository and worktree records remain valid.
