# RFC-0002: AgentRun Execution Engine Loop

## Title

AgentRun Execution Engine Loop.

## Authors

TBD.

## Status

Implemented.

## Decision Date

2026-07-11.

## Implementation

- Implementation PR:
  [#15](https://github.com/otterammo/ai-platform-prototype/pull/15)
- Merge commit:
  `4f8399724a537b38ed1ac8524544e1473464279b`
- Implementation version: Platform Specification `v1.3.0`
- Implementation date: 2026-07-12
- Superseded sections: none. The implementation scope and acceptance-test
  sections remain historical implementation guidance; the normative contract is
  the Platform Specification `v1.3.0` text.

## Governance Review

ADR coverage:

- [ADR 0009: Durable AgentRun Execution Loop](../adr/0009-durable-agentrun-execution-loop.md)
  records durable execution state, ExecutionFrame as an internal concept,
  crash-safe resume, non-destructive replay, and single-owner execution.
- [ADR 0010: Execution Budget And Terminal State Enforcement](../adr/0010-execution-budget-and-terminal-state-enforcement.md)
  records Execution Engine ownership of budget enforcement and terminal-state
  selection.

Specification consistency review:

- [Execution Engine](../spec/023-execution-engine.md) contains the normative
  loop, budgets, completion, failure, resume, retry, cancellation, timeout,
  ToolInvocation ownership, request-input deferral, leases, events, and trace
  semantics.
- [AgentRuns](../spec/008-agent-runs.md), [Runtime](../spec/013-runtime.md),
  [Events](../spec/014-events.md), [Tool Invocations](../spec/021-tool-invocations.md),
  [Decisions](../spec/022-decisions.md), and [Glossary](../spec/018-glossary.md)
  align with the implemented RFC-0002 boundary.

## Formal Review Conclusion

Accept.

RFC-0002 is implemented as the Platform Specification `v1.3.0` execution-loop
contract by PR #15.

The merged implementation provides the local Execution Engine loop, persisted
ExecutionFrame-equivalent status data, deterministic ToolInvocation creation,
Observation delivery, explicit completion and failure Decisions, budget
enforcement, cancellation, timeout handling, invalid Decision retries,
crash-safe resume behavior, and trace/events for the implemented loop.

`request_input` remains specified but unsupported by the initial implementation.
The implementation rejects `request_input` as an unsupported Decision type
rather than entering partial waiting behavior.

## Motivation

After ToolInvocation exists as a first-class resource and Observation data is
embedded in ToolInvocation status, the platform needs a bounded Execution Engine
loop that can continue an AgentRun after each Observation. This loop must be
explicit, observable, crash-resumable, policy-governed, and safe to terminate.

Without a defined loop, each runtime implementation would decide independently
when to call the Model again, how to resume after approval, how to prevent
unbounded execution, and how to distinguish success from inactivity.

## Background

RFC-0001 defines one governed tool operation:

```text
AgentRun
-> ToolInvocation
-> Policy
-> Tool Runtime
-> Observation
```

RFC-0002 defines how one AgentRun execution attempt may continue across multiple
Decisions. The canonical Decision protocol is defined by the Platform
Specification [Decisions](../spec/022-decisions.md) chapter. Provider-specific
model message adaptation remains part of RFC-0004.

The responsibility chain is:

```text
Mission
-> AgentRun
-> Execution Engine
-> Pilot
-> Model
-> Decision
-> Execution Engine
-> Platform resources, status, events, and trace
```

The Execution Engine owns control flow. Pilot owns reasoning strategy, prompt
construction, model selection, provider adaptation, and response interpretation.
Model produces structured output that Pilot adapts into a provider-neutral
Decision. The Execution Engine validates that Decision and creates or updates
platform state such as ToolInvocation, Artifact, AgentRun status, and Events.

## Goals

- Define AgentRun execution-loop lifecycle semantics.
- Define continuation after embedded Observation data.
- Use the provider-neutral Decision protocol at the loop boundary.
- Define loop termination conditions.
- Define execution budgets, timeouts, retry boundaries, and cancellation.
- Define durable resume and non-destructive replay semantics.
- Preserve status and events for every iteration, Decision, ToolInvocation, and
  terminal outcome.
- Preserve the AgentRun-only execution boundary.

## Non-Goals

This RFC does not define:

- Built-in filesystem, git, or shell Tool Runtime behavior; that remains
  RFC-0003.
- Provider-specific structured response protocols beyond the minimal adapter
  required for tests; full protocol/provider work remains RFC-0004.
- The complete real-model multi-turn workload; that remains RFC-0005.
- Autonomous planning across Missions, Fleets, or Agents.
- A public ExecutionFrame, Conversation, Session, or Continuation resource.
- Transactional rollback of workspace changes.

## Execution Engine State Machine

AgentRun execution-loop state is separate from ToolInvocation phase. The
Execution Engine MUST use the following state machine or semantically equivalent
states.

```text
Pending
  |
  v
Starting
  |
  v
AwaitingDecision
  |
  v
DecisionReady
  |
  v
ProcessingDecision
  |-- invoke_tool ----> WaitingForTool ----> WaitingForObservation
  |                                           |
  |                                           v
  |                                      AwaitingDecision
  |
  |-- request_input --> WaitingForInput ----> AwaitingDecision
  |
  |-- complete -------> Finalizing ---------> Succeeded
  |
  `-- fail -----------> Failed
```

Terminal states are:

- `Succeeded`
- `Failed`
- `Cancelled`
- `TimedOut`
- `BudgetExceeded`

`WaitingForApproval` is a waiting condition applied while a ToolInvocation is
blocked by Policy. It is not a separate terminal phase. While approval is
pending, the AgentRun remains in the execution-loop state that owns the blocked
work, normally `WaitingForTool`, and status MUST reference the pending Approval
or ToolInvocation condition.

### Required Invariants

- An AgentRun MUST have at most one active Decision.
- An AgentRun MUST have at most one active ToolInvocation created from that
  Decision.
- A completed ToolInvocation MUST NOT execute again.
- A terminal AgentRun MUST NOT resume automatically.
- Every state transition MUST emit an Event.
- `status.observedGeneration` MUST identify the AgentRun generation being
  executed.
- Only the Execution Engine may advance execution-loop state.
- Pilot and Model MUST NOT create platform Resources directly.
- ToolInvocation identity MUST be stable across retries, reconciliation, and
  crash recovery for the same Decision.

### Transition Table

| Current state | Triggering input | Next state | Side effects | Emitted events | Retry behavior |
| --- | --- | --- | --- | --- | --- |
| `Pending` | AgentRun is scheduled, Context is Ready, and the worker obtains the execution lease | `Starting` | Persist lease holder, fencing token or execution epoch, effective AgentRun generation, and initial budget snapshot | `ExecutionEngineStarted` | Lease acquisition MAY retry without side effects |
| `Starting` | Startup validation succeeds | `AwaitingDecision` | Persist execution-loop status, effective Pilot configuration version, Context reference and revision, and correlation ID | `ExecutionFramePrepared` | Startup validation failures become `Failed` unless retry policy marks the missing dependency retryable |
| `AwaitingDecision` | Engine prepares the next model request | `DecisionReady` | Persist the ExecutionFrame, model invocation metadata, attempt number, and budget usage; invoke Pilot/Model | `DecisionRequested`, `DecisionProduced`, `ExecutionBudgetUpdated` | Model transport failures MAY retry with the same persisted input frame and a new attempt number |
| `DecisionReady` | Decision parses and validates | `ProcessingDecision` | Persist validated Decision envelope, Decision number, Decision attempt, and redacted summary | `DecisionValidated` | Not applicable after successful validation |
| `DecisionReady` | Decision parse, schema, version, semantic, capability, argument, budget, or policy-admission validation fails | `AwaitingDecision` or `Failed` | Persist rejection reason and validation feedback | `DecisionRejected`, `ExecutionRetryScheduled` or `ExecutionFailed` | Invalid Decisions MAY be repaired by invoking the Pilot again, subject to `maxDecisionFailures` |
| `ProcessingDecision` | Valid `invoke_tool` Decision | `WaitingForTool` | Create or reconcile exactly one deterministic ToolInvocation; evaluate Policy; attach WaitingForApproval condition when required | `ToolInvocationCreated`, policy event, `ExecutionBudgetUpdated` | Duplicate ToolInvocations MUST NOT be created; infrastructure retry preserves operation identity and attempt number |
| `WaitingForTool` | ToolInvocation requires approval | `WaitingForTool` | Persist pending Approval reference and stop before side effects | approval requested event | Engine waits; it MUST NOT repeatedly invoke the Model while approval is pending |
| `WaitingForTool` | ToolInvocation reaches a terminal phase with an embedded Observation | `WaitingForObservation` | Persist ToolInvocation terminal reference and embedded Observation metadata | `ToolInvocationObserved` | Completed ToolInvocation MUST NOT execute again |
| `WaitingForObservation` | Observation is consumed into the next frame | `AwaitingDecision` | Persist Observation delivery marker and update last successful iteration when applicable | `ObservationDelivered`, `ExecutionFramePrepared` | Observation delivery MAY retry only as a status/event write; it MUST NOT re-execute the tool |
| `ProcessingDecision` | Valid `request_input` Decision | `WaitingForInput` | Persist the input request and expose it through API/CLI | `InputRequested` | Engine waits; it MUST NOT repeatedly invoke the Model while waiting |
| `WaitingForInput` | User input is received | `AwaitingDecision` | Persist the response and include it in the next ExecutionFrame | `InputReceived`, `ExecutionFramePrepared` | Duplicate input delivery MUST be idempotent |
| `ProcessingDecision` | Valid `complete` Decision | `Finalizing` | Persist completion Decision; validate required outputs; create final summary Artifact unless disabled | `ExecutionFinalizing` | Finalization failures MAY retry only finalization steps that have not succeeded |
| `Finalizing` | Required outputs and final Artifact are recorded | `Succeeded` | Persist terminal status, completed outputs, final budget usage, and terminal reason | `ExecutionCompleted` | Terminal AgentRun MUST NOT resume automatically |
| `Finalizing` | Required outputs cannot be finalized | `Failed` | Persist terminal failure, retryability, diagnostics, and partial output references | `ExecutionFailed` | Retry follows finalization retry policy; exhaustion becomes `Failed` |
| `ProcessingDecision` | Valid `fail` Decision | `Failed` | Persist terminal failure reason, retryability, diagnostics, and partial output references | `ExecutionFailed` | Terminal AgentRun MUST NOT resume automatically |
| Any non-terminal state | Cancellation is requested | `Cancelled` | Acknowledge cancellation; cancel interruptible work; prevent subsequent actions; record partial summary Artifact when meaningful work occurred | `CancellationRequested`, `CancellationAcknowledged`, `ExecutionCancelled` | Cancelled AgentRuns do not automatically retry |
| Any non-terminal state | Total wall-time deadline expires | `TimedOut` | Stop supported active work; persist timeout reason and partial state | `ExecutionTimedOut` | Per-step retry may occur before the total deadline; total deadline exhaustion is terminal |
| Any non-terminal state | A hard budget limit is reached | `BudgetExceeded` | Persist the exceeded limit, usage snapshot, and diagnostics | `ExecutionBudgetExceeded` | Budget exhaustion is terminal |
| Any active state | Worker loses lease or fencing token | Current persisted state | Worker stops initiating new side effects; another owner may recover after stale lease rules | lease lost or recovery event | New owner resumes from persisted state, not process-local memory |

## Execution Budgets

AgentRun execution budgets are enforced by the Execution Engine. Pilot and Model
MUST NOT alter budgets.

Recommended defaults:

```yaml
spec:
  execution:
    maxIterations: 50
    maxToolInvocations: 40
    maxModelInvocations: 50
    maxDecisionFailures: 3
    maxToolFailures: 5
    maxWallTimeSeconds: 1800
    maxInputTokens: 250000
    maxOutputTokens: 100000
```

FleetTemplate or Agent configuration MAY provide defaults. Mission or AgentRun
configuration MAY provide stricter values. A child configuration MUST NOT
silently increase a parent-enforced limit. Reaching any hard limit transitions
the AgentRun to `BudgetExceeded`.

Budget usage MUST be recorded in AgentRun status and Events. Token budgets MAY
be unavailable for providers that do not report usage. When token usage is
unavailable:

- Invocation and wall-time budgets remain mandatory.
- Token usage is marked `Unknown`.
- The Execution Engine MUST NOT fabricate token counts.

## Completion Semantics

Completion MUST be explicit. The Execution Engine MUST NOT infer success from
inactivity, an empty response, absence of a tool request, or natural-language
text.

A successful run requires a valid `complete` Decision:

```json
{
  "version": "v1",
  "type": "complete",
  "summary": "Implemented and tested the requested change.",
  "outputs": [
    {
      "type": "workspace-change",
      "ref": "workspace://..."
    }
  ]
}
```

Rules:

- `complete` ends the reasoning loop.
- `summary` is required.
- `outputs` MAY be empty only when the Mission permits a no-output result.
- The Execution Engine validates required Mission outputs before success.
- Existing files or ToolInvocation results MAY satisfy outputs.
- The Execution Engine creates a final summary Artifact unless the Mission
  explicitly disables it.
- Failure to finalize required outputs transitions the run to `Failed`, not
  `Succeeded`.
- The Execution Engine MUST NOT create implementation results solely from the
  completion text.

`fail` is an explicit terminal Decision:

```json
{
  "version": "v1",
  "type": "fail",
  "reason": "Required dependency is unavailable",
  "retryable": false
}
```

## Persistence, Resume, And Replay

The Execution Engine resumes from persisted execution state, not destructive
replay. Implementations MUST persist enough state to reconstruct each loop
iteration:

- Decision number and validated Decision envelope.
- ToolInvocation reference.
- Embedded Observation.
- Model invocation metadata.
- Budget usage.
- Current execution state.
- Correlation ID.
- Timestamps.
- Pilot configuration version.
- Context reference and revision.

Crash recovery rules:

- Before Decision persistence: if the model call may have occurred but no
  Decision was persisted, the engine MAY invoke the model again. This invocation
  MUST use the same persisted input frame and a new attempt number.
- After Decision persistence: the engine MUST NOT call the model again for that
  iteration. It resumes processing the persisted Decision.
- After ToolInvocation creation: the engine waits for or reconciles the
  existing ToolInvocation. It MUST NOT create a duplicate invocation.
- After ToolInvocation success: the engine consumes the persisted Observation
  and proceeds to the next iteration. It MUST NOT execute the tool again.
- After completion Decision persistence: the engine resumes finalization without
  requesting another Decision.

Replay means reconstructing history for inspection or testing. Replay MUST NOT
repeat external side effects. Events are audit records, but AgentRun,
Decision-frame, and ToolInvocation state are the operational source of truth.

## Execution Frame And Observation Window

ExecutionFrame is an internal concept for the canonical data supplied to the
Pilot for a single Decision request. It is not a public Resource in v1.3.

Each model request receives:

- Immutable Mission intent.
- Relevant ready Context snapshot.
- Agent identity and capabilities.
- Active execution budgets.
- Current workspace state summary.
- Prior Decisions.
- ToolInvocation Observations.
- User input responses.
- Prior failure information.

The bounded context policy is:

```text
Initial Context
+ Stable Mission/Agent instructions
+ Recent full execution frames
+ Older summarized execution history
```

Recommended defaults:

- Preserve the most recent 10 complete iteration frames verbatim.
- Summarize older frames.
- Never omit unresolved failures, pending approvals, current workspace changes,
  or required outputs.
- Preserve references to all full persisted records for traceability.

Context compaction MUST be deterministic for the same persisted history and
configuration.

The Pilot owns rendering this execution state into provider-specific messages.
The Execution Engine owns selecting the canonical execution data supplied to the
Pilot.

## Cancellation

Cancellation is declarative. The API and CLI SHOULD expose intent equivalent to:

```text
platform cancel agentrun <name> -n <workspace>
```

The resource representation is:

```yaml
spec:
  cancellationRequested: true
```

An equivalent controller-owned command resource MAY be used if required by
status ownership rules.

Cancellation semantics:

- The Execution Engine checks cancellation before every model invocation,
  Decision processing step, and ToolInvocation creation.
- Pending or waiting work transitions promptly to `Cancelled`.
- An active interruptible model or tool execution SHOULD be cancelled.
- A non-interruptible operation MAY finish, but no subsequent action may begin.
- Successful completion of an external side effect before cancellation remains
  recorded.
- Cancellation MUST NOT roll back side effects automatically.
- The engine SHOULD produce a partial-run summary Artifact containing completed
  actions and unresolved work.
- Cancellation MUST emit request, acknowledgment, and terminal Events.
- Cancelled AgentRuns do not automatically retry.

## Timeout Semantics

Execution defines three timeout levels:

- Per-model-invocation timeout.
- Per-tool-invocation timeout.
- Total AgentRun wall-time budget.

A timeout:

- Stops the affected operation where supported.
- Records a structured failure.
- Applies retry policy if retryable.
- Transitions to `TimedOut` when the total run deadline expires.
- MUST NOT be reported as a generic `Failed` state.

A ToolInvocation timeout remains visible on the ToolInvocation itself even if
the parent AgentRun later retries or fails.

## Retry Boundaries

Retries are owned by the Execution Engine. The Model does not decide whether
infrastructure retries occur.

Separate retry policies are defined for:

- Model transport failure.
- Invalid model response.
- ToolInvocation infrastructure failure.
- Tool-reported domain failure.
- Finalization failure.

Rules:

- Persisted successful side effects are never retried.
- Invalid Decisions may be repaired by invoking the Pilot again with validation
  feedback, subject to `maxDecisionFailures`.
- A tool-reported non-retryable failure is returned as an Observation for the
  Model to reason about.
- Infrastructure failures may be retried according to Tool policy.
- Retry attempts MUST preserve stable operation identity and use explicit
  attempt numbers.
- Retry exhaustion transitions to `Failed`, `TimedOut`, or `BudgetExceeded`, as
  appropriate.

## Decision Validation Pipeline

The mandatory Decision validation pipeline is:

```text
Provider response
-> Pilot parsing
-> Decision schema validation
-> Decision version validation
-> Decision semantic validation
-> Agent capability validation
-> Execution budget validation
-> Policy evaluation where applicable
-> Platform action
```

Failure semantics:

- Provider response cannot be parsed: `DecisionParseFailed`.
- Schema is invalid: `DecisionValidationFailed`.
- Version unsupported: `DecisionVersionUnsupported`.
- Decision type unsupported: `DecisionTypeUnsupported`.
- Tool or operation not declared: `CapabilityViolation`.
- Arguments fail Tool contract: `ToolArgumentsInvalid`.
- Policy denies action: create or update ToolInvocation as `Denied`, then
  return the denial Observation to the loop unless Policy marks it terminal.
- Repeated invalid Decisions beyond budget: AgentRun becomes `Failed`.

Unknown Decision types MUST be rejected. They MUST NOT be treated as
natural-language completion.

## ToolInvocation Semantics

One AgentRun may own many ToolInvocations. A ToolInvocation MUST NOT create a
new AgentRun.

Relationship:

```text
AgentRun
|-- Decision 1
|   `-- ToolInvocation 1
|-- Decision 2
|   `-- ToolInvocation 2
`-- Decision 3
    `-- Complete
```

Each `invoke_tool` Decision creates exactly one ToolInvocation. ToolInvocation
names or identities MUST be deterministic from:

- AgentRun identity.
- AgentRun generation.
- Iteration number.
- Decision attempt or stable Decision ID.

This prevents duplicate side effects after reconciliation or recovery.

## Request Input

`request_input` is included in RFC-0002 semantics, but the initial RFC-0002
implementation MAY defer it. If deferred, an implementation MUST reject
`request_input` as unsupported and MUST NOT provide ambiguous partial waiting
behavior.

Example:

```json
{
  "version": "v1",
  "type": "request_input",
  "prompt": "Which authentication provider should be used?",
  "required": true
}
```

Semantics when supported:

- AgentRun transitions to `WaitingForInput`.
- The question is persisted and exposed through CLI/API.
- User response is persisted and supplied in the next ExecutionFrame.
- The Model MUST NOT be repeatedly invoked while waiting.
- Optional input MAY define a timeout or default.
- Required input without a response remains waiting until cancelled or timed
  out.

## Failure And Partial Completion

AgentRun status MUST distinguish:

- Terminal reason.
- Retryability.
- Last successful iteration.
- Completed ToolInvocations.
- Unresolved ToolInvocations.
- Partial Artifacts.
- Budget usage.
- Diagnostic summary.

Partial workspace changes remain intact unless a future rollback RFC defines
transactional execution. The Execution Engine SHOULD create a failure summary
Artifact for runs that performed meaningful work before failure.

Parent Agent, Fleet, and Mission status is derived by controllers.

## Trace And Events

Trace MUST reconstruct:

```text
AgentRun started
Decision 1 produced
Decision 1 validated
ToolInvocation 1 created
Policy evaluated
Approval requested/granted, if applicable
ToolInvocation 1 executed
Observation 1 recorded
Decision 2 produced
...
Complete Decision produced
Outputs validated
Artifact created
AgentRun succeeded
```

Required events include:

- `ExecutionEngineStarted`
- `ExecutionFramePrepared`
- `DecisionRequested`
- `DecisionProduced`
- `DecisionValidated`
- `DecisionRejected`
- `ToolInvocationCreated`
- `ToolInvocationObserved`
- `ObservationDelivered`
- `ExecutionBudgetUpdated`
- `ExecutionRetryScheduled`
- `InputRequested`
- `InputReceived`
- `CancellationRequested`
- `CancellationAcknowledged`
- `ExecutionFinalizing`
- `ExecutionCompleted`
- `ExecutionFailed`
- `ExecutionCancelled`
- `ExecutionTimedOut`
- `ExecutionBudgetExceeded`

Events MUST identify:

- AgentRun.
- Iteration.
- Attempt.
- Decision type and version.
- ToolInvocation where applicable.
- Correlation ID.
- Budget snapshot.
- Reason.

Sensitive tool arguments, model prompts, secrets, and full file content MUST be
redacted or referenced rather than copied indiscriminately into Events.

## Concurrency And Leases

One AgentRun may have only one active Execution Engine owner.

The initial local implementation may use a single-process lease, but the
contract must support future distributed workers. AgentRun execution ownership
MUST define:

- Lease holder.
- Lease expiry.
- Renewal.
- Stale lease recovery.
- Fencing token or execution epoch.

A worker that loses its lease MUST stop initiating new side effects.

This may reuse existing scheduling fields or require a small AgentRun lease
structure. It MUST NOT create a separate public resource unless a future RFC
justifies that lifecycle.

## Required Specification Updates

RFC-0002 acceptance adds Platform Specification `v1.3.0`. Normative updates are
required for:

- AgentRun lifecycle.
- Execution Engine responsibilities.
- Pilot responsibilities.
- Decision handling.
- ToolInvocation ownership.
- Policy/approval waiting.
- Events.
- Trace.
- Cancellation.
- Budgets.
- Retry and recovery.
- Glossary.

A dedicated Execution Engine chapter is added because the general Runtime
chapter cannot express these semantics cleanly without obscuring runtime's other
boundaries.

## Implementation Scope

After acceptance, implementation should include:

- Execution Engine loop.
- `invoke_tool`, `complete`, and `fail`.
- Persisted execution frames or equivalent state.
- Deterministic ToolInvocation creation.
- Observation delivery.
- Budgets.
- Cancellation.
- Crash-safe resume.
- Events and trace.
- Fake Tool Runtime integration.

`request_input` is deferred from the initial implementation unless it is fully
specified and tested in the implementation slice.

Do not implement built-in filesystem, git, or shell runtimes under RFC-0002.
Those remain RFC-0003.

Do not implement provider-specific structured response protocols beyond the
minimal adapter required for tests. Full protocol/provider work remains
RFC-0004.

Do not implement the complete real-model multi-turn workload until RFC-0005.

## Migration And Backward Compatibility

Existing single-pass AgentRuns remain valid. Implementations may continue to
produce a single Artifact without entering the iterative loop when they do not
claim RFC-0002 support.

The `ai.platform/v1` resource API remains stable. RFC-0002 adds optional
AgentRun `spec.execution`, optional cancellation intent, status fields, Events,
and trace projections. Existing required field meanings do not change.

Implementations SHOULD gate iterative execution by runtime capability, Platform
Specification version, AgentRun execution settings, or Pilot capability until
the loop is implemented and tested.

Existing ToolInvocation and embedded Observation contracts from RFC-0001 remain
unchanged. RFC-0002 consumes those Observations in later iterations.

## Acceptance Tests

An implementation of RFC-0002 MUST include tests for:

- Valid state transitions and terminal-state immutability.
- One active Decision per AgentRun.
- Deterministic ToolInvocation identity for `invoke_tool`.
- Duplicate prevention after crash recovery.
- Completion requiring explicit `complete`.
- Failure requiring explicit `fail` or structured terminal reason.
- Output validation before `Succeeded`.
- Budget exhaustion to `BudgetExceeded`.
- Per-model timeout, per-tool timeout, and total wall-time timeout.
- Cancellation before model invocation, before ToolInvocation creation, while
  waiting, and while active work is running.
- Model transport retry with same input frame and new attempt number.
- Invalid Decision retry and `maxDecisionFailures` exhaustion.
- Tool infrastructure retry without re-running successful side effects.
- Tool-reported non-retryable failure delivered as Observation.
- Resume before Decision persistence, after Decision persistence, after
  ToolInvocation creation, after ToolInvocation success, and after completion
  Decision persistence.
- Trace reconstruction across Decisions, ToolInvocations, Policy, Observations,
  Artifacts, retry, cancellation, timeout, and terminal reason.
- Event payload redaction for prompts, tool arguments, secrets, and file
  content.
- Lease loss preventing new side effects.

## Accepted Decisions

- Execution Engine, not Pilot, owns iterative AgentRun control flow.
- One AgentRun execution attempt may own many ToolInvocations.
- ToolInvocation remains the governed side-effect boundary.
- Decisions are provider-neutral protocol messages, not Resources.
- Completion and failure are explicit Decisions.
- Durable execution state, not event replay, is the operational source of truth.
- ExecutionFrame is internal in `v1.3.0`.
- `BudgetExceeded` replaces vague limit-reached terminal language.
- `WaitingForApproval` is a waiting condition on blocked work, not a separate
  AgentRun terminal state.
- `request_input` semantics are specified, but initial implementation may defer
  support without partial behavior.

## Alternatives Considered

Letting Pilot own the loop was rejected because Pilot should remain stateless
reasoning, prompt, routing, provider adaptation, and response parsing logic.

Creating a new AgentRun for every ToolInvocation was rejected because it splits
one execution attempt across multiple executable resources and complicates
retry, approval, trace, and artifact semantics.

Treating model output as ToolInvocation was rejected because it couples the
model protocol to the platform resource API and weakens validation, Policy, and
trace boundaries.

Using Events as the only persistence mechanism was rejected because replay is
for inspection and testing. Operational resume needs explicit AgentRun,
Decision-frame, and ToolInvocation state.

Promoting ExecutionFrame to a public Resource was rejected for `v1.3.0` because
the initial implementation does not require independent admission, ownership,
scheduling, retention, or cross-AgentRun sharing.

## Risks

- Retrying side-effecting tools without stable identity can duplicate work.
- Approval resume can race with cancellation or timeout unless terminal
  precedence is enforced.
- Token budgets are provider-dependent and must represent unknown usage without
  fabricating counts.
- Trace gaps will make deterministic replay and review impossible.
- A future distributed worker implementation will need stronger lease storage
  than the initial local process.
