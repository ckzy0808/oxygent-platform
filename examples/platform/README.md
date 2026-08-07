# Multi-role, multi-model workflow demo

This demo runs the first platform workflow without replacing the existing MAS,
Agent, Tool, MCP, Web, CLI, or batch APIs:

```text
Idea -> Product Manager -> RequirementSpec
     -> Solution Architect -> ArchitectureDecision
     -> Technical Lead -> TaskGraph
     -> Reviewer -> ReviewReport
```

Each role has an independent `AgentProfile` and `RoleModelPolicy`. The demo
creates four non-secret `ProviderProfile` objects and resolves credentials from
environment variables only. The Reviewer policy excludes the Provider that
produced the TaskGraph whenever an eligible alternative is available.

## Configure

Copy the variable names from `.env.multi_role.example` into the repository-root
`.env` file and replace the placeholder URLs, model names, and keys. Do not
commit `.env`.

The adapter is selected explicitly by `OXYGENT_<ROLE>_PROVIDER_TYPE`:

- `openai-compatible`
- `openai-responses`
- `gemini`
- `ollama`

Native OpenAI and Anthropic identifiers are reserved in the domain model but do
not have adapters in this phase.

## Run

From the repository root:

```bash
.conda-env/bin/python examples/platform/multi_role_workflow_demo.py
```

In PyCharm, create a Python run configuration with that script path, set the
working directory to the repository root, and select `.conda-env/bin/python` as
the interpreter. The script prints the four Artifacts, sanitized model usage,
and routing decisions. It never prints resolved API keys.

The default stores are intentionally in memory. Restarting the process clears
Artifacts, usage, and traces; persistent adapters are deferred to a later phase.

## Run from the Web UI

Use the same environment variables and set `OXYGENT_ENABLE_REAL_WORKFLOW=1`,
then run:

```bash
OXYGENT_PROJECT_DEMO_PORT=18086 \
.conda-env/bin/python examples/platform/projects_web_demo.py
```

Open `http://127.0.0.1:18086/web/projects.html`, select a Project, open the
Ideas tab, enter an Idea, and choose **Start workflow**. The API returns a run
immediately; the Workflow page then follows sanitized append-only events over
SSE. Generated Artifacts, routing traces, and model usage all use the same
in-memory service stores shown by Projects, Workflows, Models, and Insights.

If `OXYGENT_ENABLE_REAL_WORKFLOW` is absent, the existing credential-free UI
demo still starts, but the launch button remains disabled and clearly reports
that real models are not configured. It never substitutes mock output for a
real workflow run.

## 代码工作区

`projects_web_demo.py` 现在默认将当前 OxyGent Git 仓库作为一个服务器批准的
代码源；浏览器只会看到源引用，不会得到本机绝对路径。打开
`/web/code.html` 后依次注册仓库、创建代码任务，再配置并运行验证方案。每个
任务都会在 `/tmp/oxygent-code-worktrees` 中创建独立 Git Worktree，源目录不会
被修改。

可以用 `OXYGENT_CODE_REPOSITORIES` 覆盖默认源，多个仓库使用系统路径分隔符；
也可以设置 `OXYGENT_DISABLE_CODE_WORKSPACE=1` 显式关闭。验证命令必须是 JSON
参数数组，当前演示服务仅批准 Python 解释器和 `git`，拒绝 Shell、管道及任意
拼接命令。
