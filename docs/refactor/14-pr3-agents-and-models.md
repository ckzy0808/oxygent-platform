# PR 3: Agents and Models

## Outcome

This PR connects the Phase 1 role/model control plane to product APIs and the
Agents and Models pages. Roles remain independent from model names; an
`AgentProfile` continues to reference a Role, Model Policy, Tool Policy, and
Prompt key.

PR 3 is additive to PR 1 and PR 2. Existing MAS, Chat/SSE, Projects, Artifacts,
Tools, MCP, and generic Agent behavior remain available.

## Backend structure

```text
oxygent/platform/
├── control_plane.py  # Explicit registries, usage, traces, health and safety gate
├── api.py            # Sanitized product API projections
└── services.py       # PlatformServices owns the control plane
```

`PlatformControlPlane` contains caller-provided registries for Providers,
Models, Roles, Agent Profiles, Role Model Policies, Tool Policies, Model Usage,
Route Traces, and Provider Adapters. Empty registries are valid, so a legacy MAS
does not need control-plane configuration.

## APIs

```text
GET   /api/v1/platform/roles
GET   /api/v1/platform/agents
GET   /api/v1/platform/tool-policies
GET   /api/v1/platform/providers
POST  /api/v1/platform/providers
PATCH /api/v1/platform/providers/{providerId}
POST  /api/v1/platform/providers/{providerId}/test-connection
GET   /api/v1/platform/models
GET   /api/v1/platform/routing-policies
GET   /api/v1/platform/usage
```

The enriched Agent response shows:

- Role and Agent identity;
- current Provider and Model;
- Fixed, Auto, or Fallback routing state;
- capabilities and Tool Policy;
- runtime status;
- token usage, estimated cost, invocation count, and success rate;
- rule-based selection reason and configured fallback chain.

Selection reasons are routing facts only. The API does not expose model private
reasoning, prompts, raw model messages, authorization headers, or resolved
credentials.

## Credential and network safety

Provider profiles store only `credentialReference`. Product mutations accept
only references beginning with `env:`, `secret:`, `vault:`, or `keychain:`.
They do not accept a raw API key value.

Provider responses contain a mask and safe reference. Legacy references that
do not use an approved scheme are returned only as
`legacy-reference:[masked]`. Query strings, fragments, and URL user information
are removed from displayed Provider base URLs.

Connection tests return only health status, latency, and a generic success or
failure message. Adapter reason text is deliberately omitted because upstream
errors can contain authorization material.

Provider mutation and health-test APIs default to disabled:

```python
PlatformControlPlane(allow_provider_mutations=False)
```

Local administration must opt in explicitly. The bundled demo enables the gate
and uses a credential-free fake health Adapter. Production deployments should
add authentication and authorization before enabling Provider mutation APIs.

## UI

### Agent Team

The Agents page displays the four-role mapping directly:

```text
Product Manager     → Provider A / Model A
Solution Architect  → Provider B / Model B
Technical Lead      → Provider C / Model C
Reviewer            → Provider D / Model D
```

The table separates Role, Agent, Provider, Model, routing state, capabilities,
Tool Policy, status, usage, cost, and success rate. “Why this model?” expands a
sanitized rule-based selection explanation and fallback chain.

### Models

The Models page has four live tabs:

- Providers: masked credentials, health, Add/Edit/Test/Disable controls;
- Models: Provider, capabilities, context, latency/cost tiers, health, roles;
- Routing Policies: primary/fallback chains, exclusions, capability and budget;
- Usage: invocations, tokens, latency, cost, status, and fallback use.

## Run locally

The PR 2 demo now also seeds the PR 3 control plane:

```bash
cd /Users/zeyucheng/Documents/oxyAgent
SETUPTOOLS_USE_DISTUTILS=stdlib PYTHONPATH=. \
  .conda-env/bin/python examples/platform/projects_web_demo.py
```

Open:

```text
http://127.0.0.1:18080/web/agents.html
http://127.0.0.1:18080/web/models.html
```

If port `18080` is already in use, set a different local demo port before
launching, for example:

```bash
OXYGENT_PROJECT_DEMO_PORT=18081 \
  SETUPTOOLS_USE_DISTUTILS=stdlib PYTHONPATH=. \
  .conda-env/bin/python examples/platform/projects_web_demo.py
```

`SETUPTOOLS_USE_DISTUTILS=stdlib` avoids a warning from this workspace's old,
partial setuptools installation; it is not required by OxyGent application
code.

## Migration and rollback

No database migration is required. Provider, Model, Role, Agent, policy, usage,
and trace records continue to use the Phase 1 in-memory registries. Persistent
registry and usage adapters remain future work.

Removing the injected platform router or supplying an empty control plane
returns the UI to its explicit empty/not-configured state. Existing LLM and MAS
configuration is not rewritten.

## Browser verification

The local demo was verified at `http://127.0.0.1:18081` while an existing
process retained port `18080`. The verification covered all four role/model
mappings, Provider health checks, model assignments, routing policies, usage,
Projects and Chat shell regressions, credential masking, and an empty browser
error console.

- `screenshots/pr3-agent-team.png`
- `screenshots/pr3-providers.png`
- `screenshots/pr3-models.png`
- `screenshots/pr3-routing-policies.png`
- `screenshots/pr3-provider-modal.png`
