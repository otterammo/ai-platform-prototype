# Tool Invocations

## Purpose

ToolInvocation represents one structured request to execute a Tool operation for
an AgentRun. It is the runtime-side effect boundary where model-directed or
platform-directed intent becomes a governed platform action.

Observation represents the structured result of a ToolInvocation and preserves
the execution outcome for status, API clients, trace reconstruction, and future
Pilot continuation semantics.

Runtime MUST execute tools only through ToolInvocation resources. Runtime MUST
NOT treat process-local logs, provider-native tool-call objects, or
natural-language text as substitutes for ToolInvocation state.

## Scope And Ownership

ToolInvocation is Workspace-scoped. A ToolInvocation MUST be owned by exactly
one AgentRun in the same Workspace and MUST reference the Tool and operation it
requests.

Observation is Workspace-scoped. An Observation MUST be owned by the AgentRun
that received it and MUST reference the ToolInvocation that produced it.

ToolInvocation and Observation resources MUST NOT cross Workspace boundaries.
Tool outputs that reference files, commits, logs, or external objects MUST use
references scoped or admitted for the owning Workspace.

## ToolInvocation Spec

ToolInvocation spec MUST include:

- AgentRun reference.
- Tool identity or Tool reference.
- Operation name.
- Structured arguments.
- Correlation identifier.
- Idempotency key for side-effecting operations when one can be derived.

ToolInvocation spec SHOULD include requested risk level, expected sandbox,
timeout override, retry intent, and output handling preferences when those values
are not fully determined by the Tool contract.

ToolInvocation spec MUST be immutable after creation.

## Lifecycle

ToolInvocation lifecycle SHOULD include:

- Requested: runtime has recorded the structured request.
- Validated: arguments satisfy the Tool operation schema.
- Authorized: policy has allowed execution.
- WaitingForApproval: policy requires Approval before execution.
- Running: a Tool Runtime is executing the operation.
- Succeeded: execution completed successfully.
- Failed: execution completed with an error.
- Denied: policy denied execution.
- TimedOut: execution exceeded its effective timeout.
- Cancelled: execution stopped because the AgentRun or operation was cancelled.

Implementations MAY use additional phases, but MUST preserve equivalent terminal
conditions for succeeded, failed, denied, timed out, and cancelled outcomes.

No side-effecting operation may start before validation and policy authorization
or approval completion.

Once a ToolInvocation reaches a terminal phase, its terminal result MUST NOT be
mutated. Corrections MUST be represented by a later event, Observation, or
replacement ToolInvocation.

## Observation Model

An Observation MUST contain:

- ToolInvocation reference.
- AgentRun reference.
- Summary.
- Structured payload matching the Tool operation output schema when execution
  succeeds.
- Error reason and message when execution fails, is denied, times out, or is
  cancelled.
- Redaction metadata when arguments or outputs were withheld by policy.

An Observation SHOULD include output references for large data rather than
embedding unbounded payloads. Output references MUST remain scoped or admitted
for the owning Workspace.

Runtime MUST preserve enough Observation data in resources or API projections for
trace reconstruction. Future Pilot continuation semantics are defined outside
this framework chapter.

## Tool Contract

Every Tool definition that can be executed by runtime MUST define:

- Supported operations.
- Input schema for each operation.
- Output schema for each operation.
- Risk level.
- Side effects.
- Timeout.
- Retry policy.
- Sandbox requirements.
- Idempotency behavior.
- Redaction requirements.

Runtime MUST validate ToolInvocation arguments against the operation input schema
before policy authorization and execution. Runtime MUST reject or fail invalid
ToolInvocations without invoking the Tool Runtime.

## Runtime Interface

Runtime MUST execute one admitted ToolInvocation through this framework by:

1. Loading the AgentRun and ToolInvocation.
2. Validating arguments against the Tool contract.
3. Evaluating Policy.
4. Pausing for Approval when Policy requires approval.
5. Executing the authorized Tool operation through the selected Tool Runtime.
6. Recording the Observation.
7. Updating ToolInvocation and AgentRun status.
8. Emitting events.

Runtime MUST NOT perform Mission, Fleet, or Agent planning while executing a
ToolInvocation.

The structured protocol that converts Model decisions into ToolInvocation
requests is defined separately from this framework.

## Tool Runtime Interface

Tool Runtimes execute authorized ToolInvocation operations. A Tool Runtime MUST
accept validated structured arguments and return output that conforms to the Tool
operation output schema or a structured error.

Tool Runtimes MUST report enough metadata for runtime to record status, events,
Observations, output references, timeout state, and redaction metadata.

Concrete built-in Tool Runtime contracts are intentionally out of scope for this
chapter.

## Policy

Every ToolInvocation MUST pass through Policy before execution. A Policy
decision MUST be allow, deny, or require approval.

Denied ToolInvocations MUST NOT execute. Approval-required ToolInvocations MUST
pause the AgentRun before the guarded side effect occurs. Runtime MUST NOT retry,
rename, reshape, or route a denied ToolInvocation to bypass Policy.

## Safety

Runtime MUST enforce:

- Workspace isolation.
- Tool operation validation.
- Per-invocation timeout.
- Cancellation.
- Redaction.
- Idempotency metadata for side-effecting operations when one can be derived.
- Policy authorization before side effects.

Tool-specific sandbox requirements are defined by Tool contracts and concrete
Tool Runtime specifications.

## Events And Trace

ToolInvocation execution MUST emit events for requested, validated, authorized,
denied, approval waiting, started, completed, failed, timed out, cancelled, and
Observation recorded states when those states occur.

ToolInvocation events MUST include correlation identifier, Workspace, AgentRun,
ToolInvocation, Tool, operation, and runtime or provider actor. Sensitive
arguments and output MUST be redacted according to Policy.

Trace views MUST be able to reconstruct each tool step, including arguments or
redacted argument metadata, policy decision, execution result, Observation
summary, and related Artifacts.
