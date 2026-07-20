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
