# AI Platform Prototype

A functional prototype of a declarative AI orchestration platform inspired by Kubernetes.

## Start Here

New users should start with the Day 0 tutorial. It runs fully offline with the
stub model and takes you from clone to completed Mission artifact.

### Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
platform version
platform health
```

### First Mission

```bash
mkdir -p day0
cp -R docs/tutorials/assets/day0/. day0/
export AI_PLATFORM_DB=sqlite:///./platform.db
export AI_PLATFORM_ROOT=.platform
platform apply day0/workspace.yaml
platform apply day0/knowledge.yaml
platform knowledge index -n day0
platform apply day0/mission.yaml
platform wait mission implement-login-page -n day0 --for phase=Waiting --reconcile --timeout 30
platform approvals
```

Approve the pending approval, then wait for completion:

```bash
platform approve <approval-name> --by day0-user --reason "Day 0 tutorial"
platform wait mission implement-login-page -n day0 --for phase=Completed --reconcile --timeout 30
platform get artifacts -n day0
platform trace mission implement-login-page -n day0
```

### Learn More

Follow the full [Day 0 tutorial](docs/tutorials/README.md) for Workspace,
Knowledge, Mission, reconciliation, approvals, trace, artifacts, and cleanup.
The engineering specification and governance docs live in [docs](docs/README.md).

## Resource Model

The prototype treats AI work as resources:

- `Workspace` defines a project boundary and storage root.
- `Mission` declares a desired outcome.
- `FleetTemplate` declares the Agent topology for a class of work.
- `Fleet` is instantiated from a Mission and FleetTemplate.
- `Capability`, `Tool`, and `Model` describe what Agents need and how the platform can satisfy it.
- `AgentRun` records a scheduled execution for an Agent.
- `Policy` declares admission rules for runtime actions.
- `Approval` records a pending, approved, or rejected action decision.
- `Agent` is created by the Fleet controller and executes through an embedded `Pilot`.
- `Knowledge` records describe workspace knowledge nodes separately from manifests.
- `KnowledgeIndex` manages indexed markdown knowledge sources for retrieval.
- `Context` records the assembled knowledge context consumed by a Mission run.
- `Artifact` records files produced by AgentRuns.

Resources use the shape:

```yaml
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: implement-auth
  namespace: demo
spec:
  template: software-feature
  inputs:
    prd:
      ref: knowledge://prd.md
  outputs:
    code: true
status:
  phase: Pending
```

The registry-driven reconciliation flow is:

```text
Platform -> Workspace -> Mission -> FleetTemplate -> Fleet -> Agent -> AgentRun
```

Runtime actions pass through the policy engine before side effects occur:

```text
AgentRun -> Execution Engine -> Pilot/Model -> Decision -> ToolInvocation -> Observation -> Decision -> Artifact
```

The Execution Engine owns the AgentRun loop. It persists ExecutionFrames,
validates Decisions, creates deterministic ToolInvocations, delivers
Observations to later iterations, enforces budgets/timeouts/cancellation, and
records terminal state.

Autonomous runs no longer require users to author `ToolInvocation` manifests by
hand. A Mission schedules an AgentRun; the local worker asks the configured
model for canonical Decisions; `invoke_tool` Decisions become deterministic
ToolInvocations; Observations feed the next Decision until the model returns
`complete` or `fail`.

Knowledge now flows through an indexed retrieval path before model invocation:

```text
Knowledge Storage -> KnowledgeIndexController -> ContextController -> Context -> AgentRun Worker
```

## Development Install

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Development

```bash
make fmt
make lint
make typecheck
make test
make check
```

Install the local Git hooks with:

```bash
pre-commit install
```

## Engineering Governance

The Platform Specification is the contract for future platform evolution.
Governance docs, RFCs, ADRs, the milestone roadmap, and contributor workflow
live in [docs](docs/README.md).

## Codex Repo Tools

- `AGENTS.md` defines persistent repository instructions for Codex agents.
- `.agents/skills/repo-quality` defines the repeatable quality workflow.
- `.codex/hooks.json` runs a repo quality gate on Codex stop events when project hooks are trusted.
- `.codex/agents/quality-verifier.toml` defines a focused verifier subagent for explicit delegation.

Use `/hooks` in Codex to review and trust project-local hooks after changes.

## Run The Prototype Reference

For first-time use, prefer the [Day 0 tutorial](docs/tutorials/README.md). The
commands below are a compact reference for the older demo manifest.

Apply the demo resources:

```bash
platform --db sqlite:///./platform.db --root .platform apply examples/demo/resources.yaml
```

Run one reconciliation pass:

```bash
platform --db sqlite:///./platform.db --root .platform reconcile
```

Inspect resources and events:

```bash
platform --db sqlite:///./platform.db --root .platform get missions
platform --db sqlite:///./platform.db --root .platform get fleettemplates
platform --db sqlite:///./platform.db --root .platform get capabilities
platform --db sqlite:///./platform.db --root .platform get models
platform --db sqlite:///./platform.db --root .platform get tools
platform --db sqlite:///./platform.db --root .platform get agentruns -n demo
platform --db sqlite:///./platform.db --root .platform get artifacts -n demo
platform --db sqlite:///./platform.db --root .platform get policies
platform --db sqlite:///./platform.db --root .platform get knowledge -n demo
platform --db sqlite:///./platform.db --root .platform get knowledgeindexes -n demo
platform --db sqlite:///./platform.db --root .platform knowledge index -n demo
platform --db sqlite:///./platform.db --root .platform knowledge search "authentication" -n demo
platform --db sqlite:///./platform.db --root .platform describe mission implement-auth -n demo
platform --db sqlite:///./platform.db --root .platform describe knowledgeindex default -n demo
platform --db sqlite:///./platform.db --root .platform get contexts -n demo
platform --db sqlite:///./platform.db --root .platform events
platform --db sqlite:///./platform.db --root .platform cancel agentrun <run-name> -n demo
```

Run the API:

```bash
platform --db sqlite:///./platform.db --root .platform serve
```

Useful endpoints:

- `POST /resources/apply`
- `GET /resources`
- `GET /resources/{kind}/{name}?namespace=demo`
- `DELETE /resources/{kind}/{name}?namespace=demo`
- `POST /reconcile`
- `GET /events`
- `GET /agentruns`
- `POST /agentruns/{name}/cancel?namespace=demo`
- `GET /knowledge`
- `GET /knowledge/search`
- `GET /knowledge/indexes`
- `GET /contexts/{mission}`
- `GET /approvals`
- `GET /approvals/{id}`
- `POST /approvals/{id}/approve`
- `POST /approvals/{id}/reject`
- `GET /artifacts`
- `GET /artifact-resources`

Deleting a Mission or Workspace removes the corresponding resource and Artifact records from SQLite, but it does not delete artifact files from disk.

The local prototype keeps the scheduler and worker in process. Controllers
create desired resources; the scheduler marks Pending AgentRuns as Scheduled;
the local worker resumes scheduled or waiting AgentRuns through the durable
Execution Engine and records Artifact resources only after a valid `complete`
Decision.

## Autonomous Dogfood Workload

The primary dogfood path is now Mission-driven:

```text
Apply Mission
-> approve guarded filesystem/git/shell/model actions when policy asks
-> watch the AgentRun produce Decisions, ToolInvocations, and Observations
-> inspect the final Artifact and trace
```

The initial supported workload is: inspect the repository, read `README.md`,
write `SUMMARY.md`, stage `SUMMARY.md`, commit it, and complete. Manual
ToolInvocation YAML is now an advanced debugging path for replaying one runtime
action in isolation.

## Knowledge Index And Retrieval

`KnowledgeIndex` resources declare markdown sources under a workspace `knowledge/` directory:

```yaml
apiVersion: ai.platform/v1
kind: KnowledgeIndex
metadata:
  name: default
  namespace: demo
spec:
  sources:
    - knowledge://prd.md
    - knowledge://architecture.md
    - knowledge://research.md
```

Indexes are deterministic and keyword-searchable in this prototype. They can be rebuilt explicitly with `platform knowledge index`; search and runtime execution lazily refresh missing or stale indexes. During execution, the Agent receives an assembled `Context` with sources, chunks, and provenance instead of raw file contents.

## Model Providers

The default provider is `stub`, which produces deterministic markdown artifacts without network access.

To use an OpenAI-compatible endpoint in the registry-driven path, create a `Model` resource and reference it through a Capability-compatible Pilot:

```yaml
kind: Model
metadata:
  name: gpt-coder
spec:
  config:
    provider: openai-compatible
    model: gpt-4.1-mini
    baseUrl: https://api.openai.com/v1
    apiKeyEnv: OPENAI_API_KEY
```

The runtime sends chat-completions-compatible requests to `{baseUrl}/chat/completions`.

Legacy Missions with `spec.objective` and `spec.brief` still work; they reconcile through the original single-agent path.

## Policy And Approvals

If no `Policy` resources exist, runtime actions are allowed for backward compatibility. Once a `Policy` exists, unmatched actions are denied.

Rules are evaluated in policy-name order, then rule order. The first match wins:

```yaml
apiVersion: ai.platform/v1
kind: Policy
metadata:
  name: default
spec:
  rules:
    - match:
        tool: shell
        operation: use
      requiresApproval: true
    - match:
        tool: git
        operation: push
      requiresApproval: true
    - match:
        tool: filesystem
        operation: delete
      requiresApproval: true
    - match:
        tool: knowledge
      allow: true
    - match:
        tool: model
      allow: true
    - match:
        tool: filesystem
      allow: true
    - match:
        tool: git
      allow: true
```

Agents pause in `Waiting` with a `WaitingForApproval` condition when approval is required:

```bash
platform --db sqlite:///./platform.db --root .platform approvals
platform --db sqlite:///./platform.db --root .platform describe approval approval-abc123
platform --db sqlite:///./platform.db --root .platform approve approval-abc123 --by alice
platform --db sqlite:///./platform.db --root .platform reject approval-abc123 --by alice --reason "too risky"
```
