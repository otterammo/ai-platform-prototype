# ADR 0006: ToolInvocation Boundary

## Title

ToolInvocation Boundary

## Status

Accepted

## Context

AgentRuns need to use tools without hiding side effects inside model output,
runtime logs, or provider-specific tool-call formats. Policy, approvals, trace,
replay, and audit all require a durable platform object that identifies the
requested Tool, operation, arguments, AgentRun, and execution outcome.

## Decision

ToolInvocation is the Workspace-scoped resource boundary for one governed Tool
operation owned by an AgentRun. Runtime and ToolInvocation execution components
must execute tools through ToolInvocation, validate the requested operation
against the Tool contract, evaluate Policy before side effects, persist stable
runtime identity when available, and emit events that preserve traceability.

ToolInvocation spec is immutable after creation. Corrections, retries, or
replacement actions must be represented by later events or later
ToolInvocations instead of mutating the original request.

## Consequences

Tool use becomes inspectable through resources, status, events, and traces.
Policy can evaluate a concrete action before execution, and future Execution
Engine loops can continue from durable tool results rather than process-local
state.

Tool providers must define enough contract data for validation, policy, timeout,
idempotency, redaction, and trace reporting. Runtime implementations must not
infer tool execution from natural language or provider-native tool-call objects
without creating the platform ToolInvocation contract.

## Alternatives

Recording tool actions only in logs was rejected because logs are not an API
contract and cannot reliably support policy, retry, replay, or trace
reconstruction.

Using provider-native tool-call formats as the platform contract was rejected
because the platform must keep Model providers replaceable.
