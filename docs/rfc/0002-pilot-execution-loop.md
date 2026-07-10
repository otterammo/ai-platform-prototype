# RFC-0002: Pilot Execution Loop

## Title

Pilot Execution Loop.

## Authors

TBD.

## Status

Draft.

## Motivation

After ToolInvocation and Observation exist as first-class resources, the
platform needs a bounded loop that lets a Pilot continue execution after each
Observation. This loop must be explicit, observable, and safe to terminate.

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

RFC-0002 defines how Pilot execution may continue across multiple decisions. It
does not define the Model message schema; that protocol is deferred to RFC-0004.

## Goals

- Define Pilot loop lifecycle semantics.
- Define continuation after Observation.
- Define loop termination conditions.
- Define iteration, ToolInvocation, timeout, and token-budget limits.
- Define retry behavior for loop steps.
- Preserve status and events for every continuation decision.

## Non-Goals

This RFC does not define built-in Tool Runtimes, the structured Model protocol,
or end-to-end multi-turn runtime implementation.

## Proposed Design

An AgentRun may enter a Pilot execution loop after it has Ready Context and an
effective Pilot. Each loop iteration asks the Pilot for the next decision,
handles that decision, records progress, and either continues or terminates.

The loop may pause for Approval. When approval is granted, runtime resumes from
the same waiting ToolInvocation or records why a replacement ToolInvocation was
created.

## Runtime Changes

Runtime must treat loop state as AgentRun execution state, not process-local
state. Runtime status should expose the current iteration, current
ToolInvocation when one exists, pending Approval when one exists, and terminal
reason when the loop stops.

Runtime must stop the loop when:

- The Pilot returns a final response.
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
validation failures, Policy denial, timeout, cancellation, and malformed Pilot
decisions.

## Event And Trace Changes

The event taxonomy should include:

- PilotLoopStarted
- PilotIterationStarted
- PilotIterationCompleted
- PilotLoopPaused
- PilotLoopResumed
- PilotLoopCompleted
- PilotLoopFailed
- PilotLoopCancelled
- PilotLoopLimitReached

Trace views should reconstruct loop order, continuation decisions,
Observations consumed by later iterations, retries, pauses, and terminal reason.

## Follow-On RFCs

RFC-0004 defines the structured Model protocol used by loop iterations. RFC-0005
composes the loop with ToolInvocation, built-in tools, and the protocol into the
full multi-turn Agent Runtime.

## Open Questions

- Which loop counters belong in AgentRun status versus events only?
- Should token budget be enforced by Pilot, runtime, or both?
- How should loop resume behave after a runtime worker crash?
