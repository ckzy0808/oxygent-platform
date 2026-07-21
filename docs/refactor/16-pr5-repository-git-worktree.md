# PR 5 — Repository and Git Worktree

## Outcome

PR 5 adds the first repository execution boundary without changing OxyGent's
existing MAS, Agent, Tool, MCP, Chat, SSE, CLI, or batch behavior. Repository
access is opt-in and disabled until the host application supplies both:

- an administrator-owned map of opaque repository references to local roots;
- a dedicated workspace root for linked Git worktrees.

No repository is scanned or registered automatically. Browser and model inputs
cannot submit a filesystem path as a repository source.

## New backend module

`oxygent/platform/coding.py` provides:

- `RepositorySource` and `RepositoryProfile`;
- `ChangeContract`, `CodeTask`, and `CodeTaskStatus`;
- `CodingEngine`, `CodingRunRequest`, and `CodingRunResult`;
- read-only `NativeCodingEngine` operations for metadata, tracked tree, literal
  search, file read, and an internal diff-read boundary;
- `ScopeGuard` and `ScopeViolation`;
- `WorktreeManager` and fixed-argument `run_git()`;
- concurrency-safe in-memory Repository and Code Task stores.

`PlatformServices.with_code_workspace()` is the explicit composition root.
Production applications can replace the stores and engine without changing the
API or Project domain.

## Isolation and security properties

1. A Repository is registered using an opaque `rootReference` from the server
   allow-list. The API never returns the configured source root.
2. The selected base branch must be in `allowedBaseBranches` and must resolve to
   a commit.
3. Each Code Task receives a server-generated `codex/code-*` branch and a
   separate linked worktree below the configured workspace root.
4. Git is invoked with `asyncio.create_subprocess_exec("git", *argv)` and never
   through a shell.
5. Repository-relative paths reject absolute paths, `..`, backslashes, and Git
   administration paths. Symlink targets are resolved and must stay inside the
   task worktree.
6. `.env*`, private keys, PEM files, and `.git/**` are denied by the platform in
   addition to the task Change Contract.
7. `ScopeGuard` enforces allowed paths, forbidden paths, dependency-change
   policy, changed-file limits, and diff-line limits in server code.
8. Code routes accept loopback socket peers only unless the embedding
   application declares that authorization middleware is configured. Forwarded
   headers are not trusted for this decision.

PR 5 contains no Agent file-write endpoint and no apply, discard, merge, push,
or remote mutation operation.

## API additions

- `GET /api/v1/platform/code/repository-sources`
- `GET /api/v1/platform/code/repositories`
- `POST /api/v1/platform/projects/{projectId}/repositories`
- `GET /api/v1/platform/projects/{projectId}/repositories`
- `POST /api/v1/platform/projects/{projectId}/code-tasks`
- `GET /api/v1/platform/projects/{projectId}/code-tasks`
- `GET /api/v1/platform/projects/{projectId}/code-tasks/{taskId}`
- `GET .../repository/metadata`
- `GET .../repository/tree`
- `GET .../repository/search`
- `GET .../repository/file`

Capabilities `codeWorkspace` and `gitWorktrees` now reflect runtime
configuration rather than returning a hard-coded value.

## UI behavior

`web/code.html` is now a working three-column Code Workspace:

- Repository Context shows the task branch, base commit, worktree reference,
  bounded tracked files, literal search, and safe file preview.
- Task Timeline keeps engineering phases distinct from ordinary group chat.
- Change Contract shows objective, acceptance criteria, allowed paths, hard
  limits, dependency policy, and risk.

The Chat Code-mode selectors now load registered repositories, base branches,
workflow runs, and the configured Agent team. General Chat remains unchanged.

## Demo configuration

The existing credential-free Project demo gains optional Code Workspace
configuration. It remains Project-only when these variables are absent.

```bash
OXYGENT_PROJECT_DEMO_PORT=18083 \
OXYGENT_DEMO_REPOSITORY=/absolute/path/to/an/approved/git/repository \
OXYGENT_CODE_WORKSPACE_ROOT=/absolute/path/to/a/worktree/root \
OXYGENT_DEMO_SEED_CODE_TASK=1 \
PYTHONPATH=. .conda-env/bin/python examples/platform/projects_web_demo.py
```

Open `http://127.0.0.1:18083/web/code.html`. The optional seeded Code Task is
examples-only and is not part of any domain default.

## Verification

Tests cover real temporary Git worktree creation, base commit recording,
unchanged source status, repository allow-listing, branch validation, path
traversal, platform-sensitive path denial, Scope Guard limits, loopback access,
safe repository APIs, and the Web contract.

Manual browser verification used a disposable clone and confirmed the complete
three-column repository view, 112 in-scope tracked files, the generated task
branch, base commit, worktree reference, seven engineering phases, and visible
Change Contract limits. No current source repository was registered or modified.

## Migration and rollback

Migration is additive. Existing `PlatformServices()` instances leave Code
Workspace disabled. To roll back, stop supplying the explicit Code Workspace
configuration and remove this module/router/UI commit; existing Project and Chat
data require no transformation. Linked worktree cleanup is intentionally part of
the later audited Discard lifecycle rather than an implicit PR 5 side effect.
