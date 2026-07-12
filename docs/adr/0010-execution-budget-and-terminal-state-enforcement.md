# ADR 0010: Execution Budget And Terminal State Enforcement

## Title

Execution Budget And Terminal State Enforcement

## Status

Accepted

## Context

RFC-0002 adds a durable AgentRun execution loop that may call Models, create
ToolInvocations, wait, retry, recover, and continue across multiple iterations.
Without a single owner for execution limits and terminal-state selection,
providers, Pilots, Tool Runtimes, or controllers could each interpret exhaustion
and failure differently.

Budget and terminal-state behavior must be stable because it affects retry,
trace, policy, user-facing status, and compatibility across model providers.

## Decision

The Execution Engine owns AgentRun execution budget enforcement and terminal
execution state selection.

Pilots and Models may report provider usage metadata, parse failures, refusals,
or transport failures, but they do not decide whether an AgentRun has exhausted
iteration, model invocation, ToolInvocation, Decision failure, tool failure,
wall-time, or token budgets.

Tool Runtimes report operation outcomes and timeout metadata through
ToolInvocation status and embedded Observation data. They do not transition the
owning AgentRun to `Succeeded`, `Failed`, `Cancelled`, `TimedOut`, or
`BudgetExceeded`.

The Execution Engine records budget usage and terminal reason in AgentRun
status and emits the corresponding execution events. Timeout and budget
exhaustion are first-class terminal states and MUST NOT be collapsed into a
generic `Failed` state.

## Consequences

AgentRun terminal status is consistent across providers and Tool Runtimes.

Provider replacement does not change the meaning of `BudgetExceeded`,
`TimedOut`, cancellation, explicit failure, or success.

Trace and event consumers can distinguish model refusal, invalid Decision,
ToolInvocation failure, timeout, cancellation, and budget exhaustion without
reverse-engineering provider-specific errors.

Execution Engine implementations must maintain explicit accounting for each
budget dimension they enforce. Token usage may be `Unknown` when a provider does
not report it, but invocation and wall-time budgets remain mandatory.

## Alternatives

Letting Pilot decide budget exhaustion was rejected because Pilot is responsible
for prompt construction, routing, provider adaptation, response parsing, and
Decision production, not execution control flow.

Letting provider adapters map provider failures directly to AgentRun terminal
states was rejected because it would make platform semantics provider-specific.

Reporting timeout or budget exhaustion as generic failure was rejected because
operators and retry policy need stable terminal reasons.
