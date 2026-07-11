# RFC-0002: AgentRun Execution Engine Loop

## Title

AgentRun Execution Engine Loop.

## Authors

TBD.

## Status

Draft.

## Motivation

After ToolInvocation exists as a first-class resource and Observation data is
embedded in ToolInvocation status, the platform needs a bounded Execution Engine
loop that can continue an AgentRun after each Observation. This loop must be
explicit, observable, and safe to terminate.

Without a defined loop, each runtime implementation would decide independently
when to call the Model again, how to resume after approval, and how to prevent
unbounded execution.

## Background

RFC-0001 defines one governed tool operation:

```text
AgentRun
-> ToolInvocation
-> Policy
-> Tool Runtime
-> Observation
```

RFC-0002 defines how AgentRun execution may continue across multiple Decisions.
The canonical Decision protocol is defined by the Platform Specification
[Decisions](../spec/022-decisions.md) chapter. Provider-specific model message
adaptation remains part of RFC-0004.

The intended responsibility chain is:

```text
Mission
-> AgentRun
-> Execution Engine
-> Pilot
-> Model
-> Decision
-> Execution Engine
-> Platform resources
```

The Execution Engine owns control flow. Pilot owns reasoning strategy, prompt
construction, model selection, and response interpretation. Model produces a
structured Decision. The Execution Engine interprets that Decision and creates
or updates platform resources such as ToolInvocation and Artifact.

## Goals

- Define Execution Engine loop lifecycle semantics.
- Define continuation after embedded Observation data.
- Use the provider-neutral Decision protocol at the loop boundary.
- Define loop termination conditions.
- Define iteration, ToolInvocation, timeout, and token-budget limits.
- Define retry behavior for loop steps.
- Preserve status and events for every continuation Decision.

## Non-Goals

This RFC does not define built-in Tool Runtimes, the structured Model protocol,
or end-to-end multi-turn runtime implementation.

## Proposed Design

An AgentRun may enter an Execution Engine loop after it has Ready Context and an
effective Pilot. Each loop iteration asks the Pilot for a provider-neutral
Decision, interprets that Decision into platform actions, records progress, and
either continues or terminates.

Pilot should be stateless. It builds prompts, selects or routes to a Model,
parses the structured response, and returns a Decision. Pilot does not own
iteration budget, timeout, cancellation, retry, ToolInvocation lifecycle,
Observation delivery, or terminal AgentRun state.

The Model does not produce ToolInvocation resources. The Model produces a
Decision such as:

```json
{
  "version": "v1",
  "type": "invoke_tool",
  "tool": "filesystem",
  "operation": "write",
  "arguments": {}
}
```

or:

```json
{
  "version": "v1",
  "type": "complete",
  "artifact": {}
}
```

The Execution Engine interprets `invoke_tool` Decisions by creating
ToolInvocation resources. Later Decision types, such as `delegate`, may create
other platform resources only after separate RFC and specification work.

The loop may pause for Approval. When approval is granted, runtime resumes from
the same waiting ToolInvocation or records why a replacement ToolInvocation was
created.

## Runtime Changes

The Execution Engine is a runtime actor. It must treat loop state as AgentRun
execution state, not process-local state. Runtime status should expose the
current iteration, current ToolInvocation when one exists, pending Approval when
one exists, and terminal reason when the loop stops.

The Execution Engine must stop the loop when:

- It receives a `complete` Decision.
- It receives a `fail` Decision.
- The AgentRun is cancelled.
- A required Approval is pending.
- A maximum iteration count is reached.
- A maximum ToolInvocation count is reached.
- A maximum effective token budget is reached.
- A maximum elapsed time is reached.
- A non-retryable failure occurs.

## Retry Semantics

Retries must be visible in AgentRun status or events. Retrying must not erase
prior ToolInvocations, Observations, model calls, or Artifacts.

Retry policy should distinguish Model failures, Tool Runtime failures,
validation failures, Policy denial, timeout, cancellation, and malformed
Decisions.

## Event And Trace Changes

The event taxonomy should include:

- ExecutionLoopStarted
- ExecutionIterationStarted
- DecisionProduced
- DecisionValidated
- DecisionRejected
- DecisionExecuted
- ExecutionIterationCompleted
- ExecutionLoopPaused
- ExecutionLoopResumed
- ExecutionLoopCompleted
- ExecutionLoopFailed
- ExecutionLoopCancelled
- ExecutionLoopLimitReached

Trace views should reconstruct loop order, continuation Decisions,
Observations consumed by later iterations, retries, pauses, and terminal reason.

## Follow-On RFCs

RFC-0004 defines provider adaptation for structured model output. RFC-0005
composes the Execution Engine loop with ToolInvocation, built-in tools, and the
Decision protocol into the full multi-turn Agent Runtime.

## Open Questions

- Which loop counters belong in AgentRun status versus events only?
- What token usage telemetry must Pilot return so the Execution Engine can
  enforce the effective token budget?
- How should loop resume behave after a runtime worker crash?

## Architecture Review

Date: 2026-07-11.

Recommendation: Revise RFC-0002 before acceptance. Do not implement RFC-0002
until the required specification work below is complete.

RFC-0002 fits the platform direction after one important correction: the
Execution Engine, not Pilot, owns the loop. The draft correctly depends on
RFC-0001 and the Decision protocol, but it must still specify durable iteration
state, failure semantics, event ordering, trace reconstruction, and API and CLI
contracts before acceptance.

### Review Criteria

- Architectural fit: The RFC fits the AgentRun execution boundary and the
  ToolInvocation framework if the loop remains one AgentRun execution attempt
  with many child ToolInvocations. It does not fit if Pilot becomes an
  executable actor or if every tool step creates a new AgentRun.
- Separation of concerns: Execution Engine owns iterative control flow for a
  scheduled AgentRun. Pilot owns prompt construction, model selection, model
  invocation, and structured response interpretation. Model produces Decisions.
  Controllers continue to admit, schedule, reconcile, and aggregate resource
  state.
- Interaction with RFC-0001: Each tool step should create or resume a
  ToolInvocation owned by the current AgentRun. Later iterations consume
  embedded `ToolInvocation.status.observation` data and ObservationRecorded
  events.
- Interaction with the control plane: RFC-0002 should add AgentRun status and
  events for loop progress. It should not add Mission, Fleet, or Agent planning
  behavior to runtime.
- Runtime impact: Runtime gains an Execution Engine for one AgentRun, but
  iteration state must be durable in AgentRun status, ToolInvocation resources,
  and events. Process-local loop variables are not sufficient.
- Policy impact: Every ToolInvocation still passes through Policy. The RFC also
  needs to define whether model continuation calls, token-budget exhaustion, and
  loop-limit decisions are policy inputs, runtime limits, or both.
- Trace impact: Every continuation must be reconstructable from AgentRun,
  Decision, ToolInvocation, embedded Observation, and event records. The current
  draft names trace goals but does not specify the required ordering fields.
- Events impact: The proposed event list is directionally right, but it needs
  Decision, continuation-input, limit, timeout, cancellation, retry, and resume
  events or payload fields.
- Extensibility: The RFC should leave room for future built-in Tool Runtimes
  and the structured Model protocol without defining provider-specific message
  formats.
- Backwards compatibility: Existing single-pass AgentRuns must remain valid.
  Iterative execution should be opt-in through Execution Engine capability,
  AgentRun status fields, or versioned runtime behavior.

### Design Review

Pilot should not own the execution loop. Pilot is the provider-independent
reasoning and model orchestration abstraction owned by Agent. For RFC-0002,
Pilot should be stateless: build prompt, choose Model, call Model, parse the
structured response, and return a Decision.

Execution Engine should own control flow inside the AgentRun boundary. It
executes a scheduled AgentRun, persists progress, enforces iteration budgets,
timeouts, cancellation, retry, and terminal state, calls Policy, creates
ToolInvocations, delivers Observations into later iterations, and records
traceable events.

Model output should not be treated as a ToolInvocation. Model output should be a
provider-neutral Decision. The Execution Engine interprets that Decision and
creates platform resources. That keeps the model protocol decoupled from the
platform API: Models propose; platform runtime interprets and records governed
state.

A Decision is not authority to perform a side effect. The Execution Engine must
admit Decisions into platform state through the applicable resource, validation,
policy, approval, and trace contracts before any side effect occurs.

The first implementation should represent iteration through AgentRun status,
events, and ToolInvocation children rather than introducing a new resource. That
keeps the contract compatible with ADR 0002 and RFC-0001. A new resource such
as Continuation or ExecutionFrame is justified only if continuations need their
own admission, scheduling, ownership, retention, replay, or worker handoff
semantics.

One AgentRun should contain many ToolInvocations. AgentRun represents one
execution attempt by an Agent; ToolInvocation represents one governed tool side
effect inside that attempt. Creating a new AgentRun for every ToolInvocation
would split a single attempt across multiple executable resources, complicate
retry, approval, trace, and artifact semantics, and weaken the AgentRun-only
execution boundary.

### State Machine

RFC-0002 should define loop state on AgentRun separately from ToolInvocation
phase. Recommended AgentRun loop states:

- `Running`: the Execution Engine is actively evaluating the Pilot or Model.
- `WaitingForTool`: runtime is waiting for an active ToolInvocation to reach a
  terminal phase.
- `WaitingForApproval`: runtime is paused on an Approval required by a
  ToolInvocation or other guarded runtime action.
- `Succeeded`: the Execution Engine received a `complete` Decision and required
  Artifacts are recorded.
- `Failed`: execution cannot continue because of a non-retryable failure.
- `Cancelled`: an operator or policy cancelled the AgentRun.
- `TimedOut`: the AgentRun exceeded its maximum elapsed time.
- `LimitReached`: the AgentRun reached an iteration, ToolInvocation, or token
  budget limit.

Do not add `WaitingForObservation` for v1.1-style embedded Observations unless
Observation becomes asynchronous or standalone. Do not add
`WaitingForContinuation` unless continuations are independently scheduled or
backoff-delayed; otherwise an event with the next iteration number is enough.

### Failure Model

Retry semantics must distinguish model-call failures, malformed Decisions,
ToolInvocation validation failures, Tool Runtime failures, Policy denial,
approval rejection, cancellation, timeout, and loop-limit exhaustion. Retries
must never delete prior model calls, Decisions, ToolInvocations, Observations,
Artifacts, or events.

Policy denial and approval rejection should not be retried by changing tool
name, operation, provider, or arguments. Tool Runtime retries must preserve
idempotency keys when side effects may occur. Model retries must record the
attempt number and preserve the invalid or failed Decision when Policy allows
recording it.

Cancellation must stop future continuations and attempt to cancel any in-flight
ToolInvocation. Timeout must be defined at three levels: per model call, per
ToolInvocation, and whole AgentRun loop. Maximum iterations, maximum
ToolInvocations, and token budgets must terminate deterministically with a
visible terminal reason.

Deterministic replay requires a stable ordered record of iteration inputs:
Context identity, Pilot configuration, Model identity, model request, Decision
envelope, ToolInvocation identity, embedded Observation data, retry attempts,
approvals, and terminal reason. RFC-0002 must specify how the Execution Engine
records enough of that data without persisting Decision as a Resource.

Partial completion must be visible. If an AgentRun fails after producing useful
Artifacts or Workspace changes, status and trace should identify partial
outputs, last successful iteration, current ToolInvocation, and why no further
continuation occurred.

### Resource Model

No new resource is required before the first RFC-0002 implementation. The
recommended model is:

- AgentRun owns loop status, counters, active ToolInvocation reference, pending
  Approval reference, terminal reason, and limit configuration or effective
  limits.
- Execution Engine owns loop execution but does not need a separate resource
  identity for the first implementation.
- ToolInvocation owns each tool side effect and embedded Observation result.
- Events provide ordered continuation history.

Add `Continuation`, `ExecutionFrame`, `Conversation`, or `Session` only if the
spec needs independent lifecycle, admission, retention, scheduling, or
cross-AgentRun sharing. Those requirements are not demonstrated yet.

### Required Specification Work

Before implementation, RFC-0002 requires Platform Specification updates:

- AgentRuns: loop status fields, counters, active ToolInvocation reference,
  pending Approval reference, terminal reason, retry attempt visibility, and
  new terminal states or conditions.
- Pilots: clarify any RFC-0002-specific Pilot options needed for iterative
  execution while preserving the stateless Decision-producing boundary.
- Runtime: define the Execution Engine, iterative AgentRun loop, durable resume
  behavior, limit enforcement, crash recovery, and runtime/controller
  separation.
- Policy: define policy inputs for continuations, model calls, approvals,
  denials, and loop-limit decisions.
- Events: define iteration, Decision, continuation, retry, pause, resume,
  timeout, cancellation, and limit event payloads with ordering fields.
- Trace: define how every continuation is reconstructed from AgentRun,
  Decision, ToolInvocation, embedded Observation, Artifact, Approval, and Event
  records.
- API: expose loop status, active ToolInvocation, pending Approval, limits,
  terminal reason, and trace projections.
- CLI: describe trace and describe output for iterative AgentRuns.
- Glossary: add RFC-0002-specific terms for iteration, continuation,
  loop limit, and terminal reason.
- Versioning: record the next minor Platform Specification version that adds
  Execution Engine loop semantics.

### Risks

- Putting loop ownership in Pilot would blur Agent identity, runtime execution,
  and provider strategy.
- Treating model output as ToolInvocation would couple the model protocol to the
  platform resource API and make future Decision types harder to add.
- Keeping loop state process-local would break crash recovery and traceability.
- Retrying side-effecting tools without stable idempotency can duplicate work.
- Approval resume can race with cancellation or timeout unless terminal
  precedence is specified.
- Token-budget and iteration-budget enforcement can drift if split between
  Pilot and runtime without a single effective-limit record.
- Trace gaps will make deterministic replay impossible, especially before
  RFC-0002 defines how structured Decisions are ordered with resulting
  resources and events.

### Recommendation

Revise RFC-0002. The architecture should proceed, but only after the RFC is
expanded with the state machine, failure model, durable loop state, event and
trace payloads, and required specification changes listed above.
