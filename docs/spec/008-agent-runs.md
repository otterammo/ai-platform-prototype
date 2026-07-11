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

AgentRun lifecycle SHOULD include:

- Pending: created but not ready to schedule.
- Scheduled: assigned to a worker or worker class.
- Running: runtime execution has started.
- WaitingForApproval: execution is paused pending Approval.
- Succeeded: execution completed and required Artifacts were recorded.
- Failed: execution cannot complete without a new attempt or desired-state
  change.

Implementations MAY use additional phases, but MUST preserve equivalent
conditions for scheduled, running, waiting, succeeded, and failed states.

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

## Tool Invocation Ownership

ToolInvocations created during execution MUST be owned by the AgentRun that
requested them. Observations produced by those ToolInvocations MUST be recorded
on the owning ToolInvocation status or through a later explicitly specified
Observation resource.

AgentRun status SHOULD reference active, waiting, failed, denied, and completed
ToolInvocations when they materially affect execution. Runtime MUST NOT hide
tool execution state in process-local logs when it affects AgentRun outcome.

## Retry And Idempotency

Retry MUST create a distinguishable execution attempt or update a retry counter
visible in status. Retrying MUST NOT erase prior events or prior Artifacts.

AgentRun names SHOULD be stable for a specific Agent generation and attempt.
Runtimes SHOULD derive idempotency keys from AgentRun identity and action
content when invoking side-effecting providers.

## Approval

If policy requires approval during execution, the AgentRun MUST enter a waiting
state and reference the pending Approval. Runtime MUST stop the guarded action
until approval is granted. If approval is rejected, the AgentRun MUST fail or
remain waiting according to policy.

Approval required for a ToolInvocation MUST pause the AgentRun before the tool
operation begins. Resuming an AgentRun after approval MUST continue from the same
ToolInvocation identity or record why a replacement ToolInvocation was created.

## Artifacts

Artifacts produced by an AgentRun MUST be represented as Artifact resources.
AgentRun status SHOULD reference produced Artifacts. Runtime MUST NOT claim
success before required Artifacts have been recorded.

## Events

AgentRun events MUST be emitted for creation, scheduling, start, policy
evaluation, approval waiting, model invocation, Decision handling, tool
invocation, artifact creation, completion, retry, and failure.
