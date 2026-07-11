# Control Plane

## Responsibilities

The control plane owns the declarative state machine of the platform. It MUST
provide admission, persistence, reconciliation, scheduling, policy evaluation,
status propagation, and event recording for resources in `ai.platform/v1`.

The control plane MUST make orchestration decisions from resource state. It MUST
NOT require runtime-local state to understand desired work, ownership, status, or
policy.

## Admission

Admission validates and defaults resources before persistence. Admission MUST
reject malformed resources, invalid owner relationships, forbidden status
writes, invalid scope, and references that violate platform constraints.

Admission MAY apply defaults. Defaults MUST be deterministic and MUST be visible
in the persisted resource.

## Persistence

Persistent resource state is the source of truth for the control plane.
Persistence MUST store resource identity, desired state, observed state,
generation, and ownership. It SHOULD support list, get, apply, delete, and
status update operations.

Persistence MUST preserve enough information for controllers to resume after
process restart without losing resource state.

## Controllers

Controllers reconcile resource types. A controller MUST observe resources,
compare desired and observed state, create or update dependent resources, update
status, and emit events.

Controllers MUST be level-based and idempotent. A controller SHOULD be able to
run repeatedly without creating duplicate child resources or duplicate runtime
side effects.

Controllers MUST NOT execute AgentRuns. They MAY create, schedule, and aggregate
AgentRuns.

Local single-process implementations MAY invoke runtime workers or
ToolInvocation execution components from the same outer service loop used for
reconciliation. Those components are runtime actors for specification purposes:
they MUST preserve AgentRun execution boundaries, ToolInvocation side-effect
boundaries, policy checks, status ownership, and event traceability.

## Scheduler

The scheduler selects executable AgentRuns for workers. Scheduling MUST happen
only after required Context is ready and policy-admission prerequisites have
been satisfied. Scheduling MAY assign a worker class, queue, priority, or
placement hint.

The scheduler MUST NOT invoke models, tools, or runtime side effects.

## Policy

Policy evaluation is part of the control-plane contract. Runtime actions that
may cause side effects MUST be authorized before execution. The control plane
MAY represent waiting decisions as Approval resources.

Policy decisions MUST be observable through status and events.

## Events

The control plane MUST emit events for material lifecycle transitions,
reconciliation decisions, scheduling decisions, policy decisions, waiting states,
failures, and completion. Events SHOULD include correlation data that connects
Platform, Workspace, Mission, Fleet, Agent, AgentRun, Context, Approval, and
Artifact resources.

## Reconciliation Order

The control plane SHOULD reconcile from higher-level intent toward lower-level
execution: Mission, Fleet, Agent, KnowledgeIndex, Context, AgentRun scheduling,
runtime completion, then aggregation back upward.

Specific implementations MAY use different controller order, runtime worker
placement, or concurrency, but the externally visible semantics MUST preserve
ownership, readiness, status propagation, policy enforcement, and AgentRun-only
execution.

## Exclusions

Control-plane controllers MUST NOT invoke Pilots, invoke Models, call Tools,
write Artifacts as execution output, or build runtime-local prompts. Those
actions belong to runtime actors after an AgentRun or ToolInvocation has been
scheduled, admitted, and authorized.
