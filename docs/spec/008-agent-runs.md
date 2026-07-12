# AgentRuns

## Purpose

AgentRun represents one executable attempt by an Agent. AgentRun is the only
executable resource in the platform.

Controllers, schedulers, workers, and runtimes MUST treat AgentRun as the unit
of execution, retry, idempotency, approval waiting, failure, and artifact
production.

## Scope And Ownership

AgentRun is Workspace-scoped. An AgentRun MUST be owned by exactly one Agent in
the same Workspace. AgentRun MUST reference its Agent, Mission, and Context.

An AgentRun MUST NOT execute until its referenced Context is Ready and policy
requirements for scheduling have been satisfied.

## Execution Lifecycle

AgentRun lifecycle MUST include the Execution Engine loop states defined in
[Execution Engine](023-execution-engine.md):

- Pending: created but not ready to schedule.
- Starting: a runtime owner is acquiring execution context and lease state.
- AwaitingDecision: execution is ready to request the next Decision.
- DecisionReady: a Decision exists and is ready for validation or processing.
- ProcessingDecision: the Execution Engine is interpreting a validated Decision.
- WaitingForTool: execution is waiting for the active ToolInvocation.
- WaitingForObservation: execution is consuming the ToolInvocation Observation.
- WaitingForInput: execution is waiting for external input requested by a
  Decision.
- Finalizing: completion output is being validated and recorded.
- Succeeded: execution completed and required outputs and Artifacts were
  recorded.
- Failed: execution cannot complete without a new attempt or desired-state
  change.
- Cancelled: cancellation was requested and acknowledged before completion.
- TimedOut: the total AgentRun wall-time deadline expired.
- BudgetExceeded: a hard execution budget was reached.

Implementations MAY use additional phases, but MUST preserve equivalent
conditions for active execution, waiting, finalization, succeeded, failed,
cancelled, timed out, and budget-exceeded states.

`WaitingForApproval` is a waiting condition on blocked work, normally a
ToolInvocation, and not a separate AgentRun terminal phase.

Only the Execution Engine may advance execution-loop state. A terminal AgentRun
MUST NOT resume automatically.

## Execution Spec

AgentRun `spec.execution` MAY define stricter execution budgets than inherited
FleetTemplate, Agent, or Mission defaults. A child configuration MUST NOT
silently increase a parent-enforced limit.

Recommended execution defaults are defined in
[Execution Engine](023-execution-engine.md). Pilot and Model MUST NOT alter
AgentRun execution budgets.

AgentRun `spec.cancellationRequested: true`, or an equivalent controller-owned
command resource, expresses declarative cancellation intent.

## Execution Status

AgentRun status MUST expose enough execution state for recovery, status
inspection, and trace reconstruction. Status MUST include or reference:

- current execution-loop state
- `observedGeneration` for the AgentRun generation being executed
- effective execution budget and current usage
- terminal reason and retryability when terminal
- active Decision summary when one exists
- active ToolInvocation when one exists
- pending Approval or input request when one exists
- last successful iteration
- completed and unresolved ToolInvocations
- partial Artifacts and completed outputs
- diagnostic summary
- execution owner, lease expiry, and fencing token or execution epoch when
  worker ownership is represented in status

Token usage MAY be marked `Unknown` when the provider does not report it. The
platform MUST NOT fabricate token counts.

## Scheduling

The scheduler MUST only schedule AgentRuns. It MUST NOT schedule Missions,
Fleets, or Agents.

Scheduling SHOULD consider Context readiness, policy, worker capability, runtime
availability, priority, retry policy, and placement constraints.

## Worker Ownership

A worker that accepts an AgentRun is responsible for reporting execution status
until completion, failure, or release. Worker identity SHOULD be recorded in
status or events.

Workers MUST be able to recover from duplicate delivery. Runtime side effects
SHOULD be idempotent or guarded by policy and action identity.

One AgentRun may have only one active Execution Engine owner. A worker that
loses its lease, epoch, or fencing token MUST stop initiating new side effects.

## Tool Invocation Ownership

ToolInvocations created during execution MUST be owned by the AgentRun that
requested them. Observations produced by those ToolInvocations MUST be recorded
on the owning ToolInvocation status or through a later explicitly specified
Observation resource.

AgentRun status SHOULD reference active, waiting, failed, denied, and completed
ToolInvocations when they materially affect execution. Runtime MUST NOT hide
tool execution state in process-local logs when it affects AgentRun outcome.

An AgentRun MUST have at most one active ToolInvocation created from the active
Decision. A completed ToolInvocation MUST NOT execute again.

## Retry And Idempotency

Retry MUST create a distinguishable execution attempt or update a retry counter
visible in status. Retrying MUST NOT erase prior events or prior Artifacts.

AgentRun names SHOULD be stable for a specific Agent generation and attempt.
Runtimes SHOULD derive idempotency keys from AgentRun identity and action
content when invoking side-effecting providers.

Execution-loop retry is owned by the Execution Engine. Persisted successful side
effects MUST NOT be retried. Retry attempts MUST preserve stable operation
identity and explicit attempt numbers.

## Approval

If policy requires approval during execution, the AgentRun MUST enter a waiting
state and reference the pending Approval. Runtime MUST stop the guarded action
until approval is granted. If approval is rejected, the AgentRun MUST fail or
remain waiting according to policy.

Approval required for a ToolInvocation MUST pause the AgentRun before the tool
operation begins. Resuming an AgentRun after approval MUST continue from the same
ToolInvocation identity or record why a replacement ToolInvocation was created.

While approval is pending, the Model MUST NOT be repeatedly invoked for the same
iteration.

## Artifacts

Artifacts produced by an AgentRun MUST be represented as Artifact resources.
AgentRun status SHOULD reference produced Artifacts. Runtime MUST NOT claim
success before required Artifacts have been recorded.

Completion MUST be explicit through a valid `complete` Decision. Runtime MUST
NOT infer success from inactivity, an empty response, absence of a tool request,
or natural-language text.

## Events

AgentRun events MUST be emitted for creation, scheduling, start, policy
evaluation, approval waiting, model invocation, Decision handling, tool
invocation, budget updates, input waiting, cancellation, timeout, artifact
creation, completion, retry, and failure.
