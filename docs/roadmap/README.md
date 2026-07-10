# Roadmap

The AI Platform roadmap is organized by capability milestones, not dates.
Milestones describe what must become true before the platform advances in
maturity.

## Foundation

Goals:

- Establish the normative Platform Specification.
- Establish engineering governance with RFCs, ADRs, review checklists, and
  versioning.
- Keep the prototype aligned with the specification.

Dependencies:

- Working control-plane prototype.
- Documented specification and governance process.

Exit criteria:

- Specification, RFC, ADR, roadmap, contributing, and review docs exist.
- Future significant work has a documented lifecycle.

## Control Plane

Goals:

- Mature resource admission, persistence, reconciliation, status, events, and
  ownership.
- Preserve level-based, idempotent controller behavior.

Dependencies:

- Foundation milestone complete.
- Resource versioning and compatibility rules established.

Exit criteria:

- Core resources have documented lifecycle and ownership contracts.
- Controllers emit events and status consistently.
- Reconciliation behavior is covered by tests.

## Knowledge

Goals:

- Treat knowledge, indexes, context, freshness, and provenance as first-class
  platform concepts.
- Support reliable retrieval for AgentRuns without runtime bypassing Context.

Dependencies:

- Control-plane resource lifecycle.
- Workspace boundaries and policy integration.

Exit criteria:

- KnowledgeIndex and Context behavior is specified and tested.
- Runtime consumes Context and records provenance in traceable outputs.

## Execution

Goals:

- Mature AgentRun scheduling, runtime isolation, policy enforcement, approvals,
  artifacts, and failure reporting.

Dependencies:

- Control Plane and Knowledge milestones.
- Policy and event contracts.

Exit criteria:

- AgentRun execution has clear retry, approval, artifact, and event semantics.
- Runtime remains isolated from reconciliation and admission.

## Distributed Runtime

Goals:

- Support external schedulers, queues, workers, and runtime placement while
  preserving AgentRun-only execution.

Dependencies:

- Execution milestone.
- Stable scheduling and worker contracts.

Exit criteria:

- Workers can execute scheduled AgentRuns outside the local process.
- Distributed execution preserves status, policy, events, and artifacts.

## Plugins

Goals:

- Define extension points for CLI, API projections, admission, policy, runtime,
  model providers, tool providers, and knowledge providers.

Dependencies:

- Stable extensibility and compatibility contracts.
- Versioning policy for extension APIs.

Exit criteria:

- Plugins declare permissions, extension points, and compatibility expectations.
- Plugin behavior cannot bypass core resource, policy, and trace contracts.

## Tool Ecosystem

Goals:

- Build a governed ecosystem of tool providers with explicit operations,
  inputs, outputs, side effects, and policy attributes.

Dependencies:

- Plugins milestone.
- Policy and runtime execution contracts.

Exit criteria:

- Tool providers can be registered, invoked, governed, and observed through
  platform contracts.
- Tool results can produce events and artifacts with provenance.

## Multi-Tenancy

Goals:

- Strengthen Workspace isolation, authorization, quota, retention, and
  cross-scope reference rules.

Dependencies:

- Mature resource ownership, policy, and audit trails.
- Distributed runtime boundaries.

Exit criteria:

- Tenant isolation is specified and tested.
- Cross-Workspace behavior requires explicit contracts.

## Enterprise

Goals:

- Add operational maturity for audit, compliance, lifecycle management,
  integrations, and upgrade paths.

Dependencies:

- Multi-Tenancy milestone.
- Stable versioning, deprecation, and migration processes.

Exit criteria:

- Operators can upgrade, audit, and govern the platform using documented
  contracts.
- Enterprise integrations preserve specification compatibility.
