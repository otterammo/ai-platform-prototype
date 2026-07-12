# ADR 0009: Durable AgentRun Execution Loop

## Title

Durable AgentRun Execution Loop

## Status

Accepted

## Context

RFC-0002 introduces an iterative Execution Engine loop for AgentRuns. The loop
can call Models, receive Decisions, create ToolInvocations, wait for Policy or
input, consume Observations, retry infrastructure failures, and produce terminal
Artifacts.

If loop state lives only in process memory or only in append-only Events,
workers cannot resume safely after crashes. Repeating a model call, duplicating
a ToolInvocation, or re-running a completed side effect would weaken Policy,
trace, and idempotency guarantees.

## Decision

AgentRun execution-loop state is durable operational state. The Execution
Engine resumes from persisted AgentRun, Decision-frame, and ToolInvocation
state, not from destructive replay.

Events remain immutable audit records, but they are not the operational source
of truth for deciding what side effect to perform next. Replay is for
inspection, testing, and projections. Replay MUST NOT repeat external side
effects.

ExecutionFrame is an internal persistence and prompt-assembly concept for
Platform Specification `v1.3.0`. It is not a public Resource unless a future RFC
demonstrates independent admission, ownership, scheduling, retention,
cross-AgentRun sharing, or API lifecycle requirements.

One AgentRun may have only one active Execution Engine owner. Implementations
MUST record enough lease, epoch, or fencing information to prevent a worker that
lost ownership from initiating new side effects.

## Consequences

Crash recovery can resume after Decision persistence, ToolInvocation creation,
ToolInvocation success, and completion Decision persistence without duplicating
work.

Implementations must persist Decision number, validated Decision envelope,
ToolInvocation reference, embedded Observation, model invocation metadata,
budget usage, execution-loop state, correlation ID, timestamps, Pilot
configuration version, and Context reference or revision.

Trace projections combine durable state and Events to explain what happened,
but execution correctness does not depend on replaying Events as commands.

## Alternatives

Keeping loop state only in worker memory was rejected because crashes would lose
the ordering needed to prevent duplicate model calls or tool side effects.

Using Events as the only source of truth was rejected because audit history is
not the same as an operational lock, lease, or persisted next-action record.

Promoting ExecutionFrame to a public Resource was rejected for v1.3 because the
initial contract does not require independent user admission, scheduling,
retention, or cross-AgentRun ownership.
