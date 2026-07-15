# Execution Engine

## Purpose

Execution Engine is the runtime component that owns AgentRun execution-loop
control flow. It invokes the effective Pilot, receives Decisions, validates and
interprets Decisions, creates ToolInvocations, delivers Observations to later
iterations, enforces budgets and policy boundaries, handles retry and
cancellation, and records terminal AgentRun state.

Only the Execution Engine may advance AgentRun execution-loop state. Pilot,
Model, Tool Runtime, controllers, and schedulers MUST NOT advance the loop
directly.

## State Machine

The AgentRun execution-loop state machine is:

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

Terminal states are `Succeeded`, `Failed`, `Cancelled`, `TimedOut`, and
`BudgetExceeded`. A terminal AgentRun MUST NOT resume automatically.
After an AgentRun enters a terminal state, late model responses, stale workers,
reconciliation passes, and pending ToolInvocations MUST NOT persist new
Decisions, create new ToolInvocations, deliver Observations into the run, start
new side effects, or finalize outputs.

`WaitingForApproval` is a waiting condition applied while a ToolInvocation is
blocked by Policy. It is not a separate terminal phase.

Every state transition MUST emit an Event. `status.observedGeneration` MUST
identify the AgentRun generation being executed.

## Invariants

An AgentRun MUST have at most one active Decision. An AgentRun MUST have at most
one active ToolInvocation created from that Decision. A completed
ToolInvocation MUST NOT execute again.

The Execution Engine MUST persist enough state that a replacement worker can
resume without relying on process-local memory.

Every execution mutation MUST verify that the AgentRun is non-terminal, that
the worker still owns the active execution owner and epoch, and that the
persisted phase and execution state still match the operation being completed.
A failed fence MUST fail closed and emit a structured event rather than
advancing execution.

## Execution Budgets

AgentRun execution budgets are configured under `spec.execution` or inherited
from FleetTemplate, Agent, or Mission configuration. The recommended defaults
are:

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

Pilot and Model MUST NOT alter budgets. Mission or AgentRun configuration MAY
provide stricter values. A child configuration MUST NOT silently increase a
parent-enforced limit.

Reaching a hard budget transitions the AgentRun to `BudgetExceeded`. Budget
usage MUST be recorded in AgentRun status and Events.

Token budgets MAY be unavailable for providers that do not report usage. In
that case invocation and wall-time budgets remain mandatory, token usage is
marked `Unknown`, and the Execution Engine MUST NOT fabricate token counts.

## Completion

Completion MUST be explicit. The Execution Engine MUST NOT infer success from
inactivity, an empty response, absence of a tool request, or natural-language
text.

A successful run requires a valid `complete` Decision with required `summary`
and `outputs` fields:

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

`outputs` MAY be empty only when the Mission permits a no-output result. The
Execution Engine MUST validate required Mission outputs before success. Existing
files or ToolInvocation results MAY satisfy outputs.

The Execution Engine creates a final summary Artifact unless the Mission
explicitly disables it. Failure to finalize required outputs transitions the run
to `Failed`, not `Succeeded`.

The Execution Engine MUST NOT create implementation results solely from the
completion text.

## Failure

`fail` is an explicit terminal Decision:

```json
{
  "version": "v1",
  "type": "fail",
  "reason": "Required dependency is unavailable",
  "retryable": false
}
```

AgentRun status MUST distinguish terminal reason, retryability, last successful
iteration, completed ToolInvocations, unresolved ToolInvocations, partial
Artifacts, budget usage, and diagnostic summary.

Partial workspace changes remain intact unless a future rollback contract
defines transactional execution. The Execution Engine SHOULD create a failure
summary Artifact for runs that performed meaningful work before failure.

## Resume And Replay

The Execution Engine resumes from persisted execution state, not destructive
replay. Implementations MUST persist enough state to reconstruct each loop
iteration, including Decision number, validated Decision envelope,
ToolInvocation reference, embedded Observation, model invocation metadata,
budget usage, current execution state, correlation ID, timestamps, Pilot
configuration version, and Context reference and revision.

Crash recovery rules:

- Before Decision persistence: if the model call may have occurred but no
  Decision was persisted, the engine MAY invoke the model again with the same
  persisted input frame and a new attempt number.
- While a model invocation is marked active for the current frame, the engine
  MUST NOT start a duplicate invocation for that frame. If the persisted active
  model invocation has exceeded its deadline, the run transitions according to
  timeout semantics instead of starting overlapping work.
- After Decision persistence: the engine MUST NOT call the model again for that
  iteration.
- After ToolInvocation creation: the engine waits for or reconciles the
  existing ToolInvocation and MUST NOT create a duplicate invocation.
- After ToolInvocation success: the engine consumes the persisted Observation
  and MUST NOT execute the tool again.
- After completion Decision persistence: the engine resumes finalization without
  requesting another Decision.

Replay means reconstructing history for inspection or testing. Replay MUST NOT
repeat external side effects. Events are audit records, but AgentRun,
Decision-frame, and ToolInvocation state are the operational source of truth.

## Execution Frame

ExecutionFrame is an internal concept for the canonical data supplied to the
Pilot for one Decision request. It is not a public Resource in Platform
Specification `v1.3.0`.

Each model request receives immutable Mission intent, relevant ready Context
snapshot, Agent identity and capabilities, active execution budgets, current
workspace state summary, prior Decisions, ToolInvocation Observations, user
input responses, and prior failure information.

The bounded context policy is initial Context plus stable Mission/Agent
instructions, recent full execution frames, and older summarized execution
history. Implementations SHOULD preserve the most recent 10 complete iteration
frames verbatim and summarize older frames.

Context compaction MUST be deterministic for the same persisted history and
configuration. It MUST NOT omit unresolved failures, pending approvals, current
workspace changes, or required outputs. It MUST preserve references to all full
persisted records for traceability.

Pilot owns provider-specific rendering. Execution Engine owns selection of the
canonical execution data supplied to Pilot.

## Cancellation

Cancellation is declarative. AgentRun `spec.cancellationRequested: true`, or an
equivalent controller-owned command resource, expresses cancellation intent.

The Execution Engine checks cancellation before every model invocation, Decision
processing step, and ToolInvocation creation. Pending or waiting work
transitions promptly to `Cancelled`.

An active interruptible model or tool execution SHOULD be cancelled. A
non-interruptible operation MAY finish, but no subsequent action may begin.
Successful completion of an external side effect before cancellation remains
recorded. Cancellation MUST NOT roll back side effects automatically.

The engine SHOULD produce a partial-run summary Artifact containing completed
actions and unresolved work. Cancellation MUST emit request, acknowledgment, and
terminal Events. Cancelled AgentRuns do not automatically retry.

## Timeouts

The execution contract defines per-model-invocation timeout,
per-tool-invocation timeout, and total AgentRun wall-time budget.

A timeout stops the affected operation where supported, records a structured
failure, applies retry policy if retryable, and transitions to `TimedOut` when
the total run deadline expires. Timeout MUST NOT be reported as a generic
`Failed` state.

A ToolInvocation timeout remains visible on the ToolInvocation itself even if
the parent AgentRun later retries or fails.

If a model response or model invocation failure returns after the AgentRun has
already become terminal, the Execution Engine MUST discard it, leave the
terminal phase and diagnostics unchanged, and emit `LateModelResponseDiscarded`
with AgentRun, model invocation, attempt, execution epoch, terminal phase, and
reason fields.

## Retry Boundaries

Retries are owned by the Execution Engine. The Model does not decide whether
infrastructure retries occur.

The Execution Engine MUST distinguish retry policies for model transport
failure, invalid model response, ToolInvocation infrastructure failure,
tool-reported domain failure, and finalization failure.

Persisted successful side effects are never retried. Invalid Decisions may be
repaired by invoking the Pilot again with validation feedback, subject to
`maxDecisionFailures`. A tool-reported non-retryable failure is returned as an
Observation for the Model to reason about. Infrastructure failures may be
retried according to Tool policy.

Retry attempts MUST preserve stable operation identity and use explicit attempt
numbers. Retry exhaustion transitions to `Failed`, `TimedOut`, or
`BudgetExceeded`, as appropriate.

## Decision Validation Pipeline

The mandatory pipeline is:

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

Failure reasons are:

- `DecisionParseFailed`
- `DecisionValidationFailed`
- `DecisionVersionUnsupported`
- `DecisionTypeUnsupported`
- `CapabilityViolation`
- `ToolArgumentsInvalid`

Policy denial creates or updates a ToolInvocation as `Denied` and returns the
denial Observation to the loop unless Policy marks it terminal.

Unknown Decision types MUST be rejected and MUST NOT be treated as
natural-language completion. Repeated invalid Decisions beyond budget make the
AgentRun `Failed`.

## ToolInvocation Ownership

One AgentRun may own many ToolInvocations. Each `invoke_tool` Decision creates
exactly one ToolInvocation. A ToolInvocation MUST NOT create a new AgentRun.

ToolInvocation names or identities MUST be deterministic from AgentRun identity,
AgentRun generation, iteration number, and Decision attempt or stable Decision
ID. This prevents duplicate side effects after reconciliation or recovery.

## Request Input

`request_input` is part of the Decision protocol and the execution state
machine, but implementations MAY defer support. If deferred, an implementation
MUST reject `request_input` as unsupported and MUST NOT provide ambiguous
partial waiting behavior.

When supported, `request_input` transitions the AgentRun to `WaitingForInput`,
persists the question, exposes it through API/CLI, persists the user response,
and supplies the response in the next ExecutionFrame. The Model MUST NOT be
repeatedly invoked while waiting.

## Leases

One AgentRun may have only one active Execution Engine owner.

AgentRun execution ownership MUST define lease holder, lease expiry, renewal,
stale lease recovery, and fencing token or execution epoch. A worker that loses
its lease MUST stop initiating new side effects.

The initial local implementation may use a single-process lease, but the
contract must support future distributed workers.

Runtime-created ToolInvocations MUST carry enough durable execution-fence data
to verify that they belong to the current AgentRun epoch before execution
starts. If the parent AgentRun is terminal, or the ToolInvocation belongs to a
stale epoch, the ToolInvocation transitions to a non-executed terminal state and
records an Observation explaining the fence. Completed side effects are not
replayed or compensated.

## Events And Trace

Execution Engine MUST emit Events for state transitions and material execution
facts. Required execution-loop events are defined in
[Events](014-events.md).

Trace projections MUST reconstruct Decision order, validation, ToolInvocation
creation, policy evaluation, approval waiting, tool execution, Observation
delivery, retries, budget updates, input waits, cancellation, timeout,
finalization, Artifacts, and terminal reason.
