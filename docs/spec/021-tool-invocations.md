# Tool Invocations

## Purpose

ToolInvocation represents one structured request to execute a Tool operation for
an AgentRun. It is the runtime-side effect boundary where model-directed or
platform-directed intent becomes a governed platform action.

Observation represents the structured result of a ToolInvocation and preserves
the execution outcome for status, API clients, trace reconstruction, and future
Pilot continuation semantics.

Runtime MUST execute tools only through ToolInvocation resources. Runtime MUST
NOT treat Decisions, process-local logs, provider-native tool-call objects, or
natural-language text as substitutes for ToolInvocation state.

## Scope And Ownership

ToolInvocation is Workspace-scoped. A ToolInvocation MUST be owned by exactly
one AgentRun in the same Workspace and MUST reference the Tool and operation it
requests.

A ToolInvocation MUST NOT create a new AgentRun. One AgentRun may own many
ToolInvocations, but the Execution Engine may have at most one active
ToolInvocation for the active Decision.

Observation data is embedded in `ToolInvocation.status.observation` for v1.1.
An embedded Observation MUST describe the ToolInvocation that produced it
through the containing ToolInvocation resource.

ToolInvocation resources MUST NOT cross Workspace boundaries. Embedded
Observation data and tool outputs that reference files, commits, logs, or
external objects MUST use references scoped or admitted for the owning
Workspace.

## ToolInvocation Spec

ToolInvocation spec MUST include:

- AgentRun reference.
- Tool identity or Tool reference.
- Operation name.
- Structured arguments.

ToolInvocation MUST have a stable correlation identifier in metadata, status, or
events. ToolInvocation spec MAY include a requested correlation identifier.

ToolInvocation spec SHOULD include an idempotency key for side-effecting
operations when one can be derived. It MAY include requested risk level, timeout
override, and output handling preferences when those values are not fully
determined by the Tool contract.

For ToolInvocations created from `invoke_tool` Decisions, ToolInvocation name or
identity MUST be deterministic from AgentRun identity, AgentRun generation,
iteration number, and Decision attempt or stable Decision ID.

ToolInvocation spec MUST be immutable after creation.

## Lifecycle

ToolInvocation lifecycle SHOULD include:

- Pending: the request exists but has not been processed.
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

Implementations MAY fold requested or validated states into resource creation,
events, or authorization. They MAY use additional phases, but MUST preserve
equivalent terminal conditions for succeeded, failed, denied, timed out, and
cancelled outcomes.

No side-effecting operation may start before validation and policy authorization
or approval completion.

Once a ToolInvocation reaches a terminal phase, its terminal result MUST NOT be
mutated. Corrections MUST be represented by a later event, updated projection,
future Observation resource, or replacement ToolInvocation.

A completed ToolInvocation MUST NOT execute again during retry, resume,
reconciliation, or replay.

## Embedded Observation Model

An embedded Observation MUST contain:

- Summary.
- Structured payload matching the Tool operation output schema when execution
  succeeds and a schema is defined.
- Error reason and message when execution fails, is denied, times out, or is
  cancelled.
- Redaction metadata when arguments or outputs were withheld by policy.

An embedded Observation SHOULD include output references for large data rather
than embedding unbounded payloads. Output references MUST remain scoped or
admitted for the owning Workspace.

Runtime MUST preserve enough Observation data in resources or API projections for
trace reconstruction. Future Pilot continuation semantics are defined outside
this framework chapter.

## Tool Contract

Every Tool definition that can be executed by runtime MUST define:

- Supported operations.
- Effective risk level.
- Effective timeout.

Each executable operation SHOULD define input schema and output schema. Runtime
MUST validate ToolInvocation arguments against the operation input schema before
policy authorization and execution when an input schema is defined. Runtime MUST
reject or fail invalid ToolInvocations without invoking the Tool Runtime.

Tool definitions SHOULD describe side effects, retry policy, sandbox
requirements, idempotency behavior, and redaction requirements when those
attributes are known. Concrete built-in Tool Runtime specifications may promote
those attributes to required fields for specific tools.

## Runtime Interface

Runtime MUST execute one admitted ToolInvocation through this framework by:

1. Loading the AgentRun and ToolInvocation.
2. Validating arguments against the Tool contract when a schema is defined.
3. Evaluating Policy.
4. Pausing for Approval when Policy requires approval.
5. Executing the authorized Tool operation through the selected Tool Runtime.
6. Recording the embedded Observation.
7. Updating ToolInvocation and AgentRun status.
8. Emitting events.

Runtime MUST NOT perform Mission, Fleet, or Agent planning while executing a
ToolInvocation.

The Decision protocol that converts Model intent into Execution Engine input is
defined separately from this framework. An `invoke_tool` Decision becomes a
ToolInvocation only after Execution Engine validation and interpretation.

Each `invoke_tool` Decision creates exactly one ToolInvocation. Runtime MUST
reconcile an existing ToolInvocation after recovery rather than create a
duplicate invocation.

## Tool Runtime Interface

Tool Runtimes execute authorized ToolInvocation operations. A Tool Runtime MUST
accept validated structured arguments and return a structured Observation result
or a structured error. Runtime SHOULD validate successful output against the
operation output schema when one is defined.

Tool Runtimes MUST report enough metadata for runtime to record status, events,
embedded Observations, output references, timeout state, and redaction metadata.

Concrete built-in Tool Runtime contracts are intentionally out of scope for this
chapter.

## Policy

Every ToolInvocation MUST pass through Policy before execution. A Policy
decision MUST be allow, deny, or require approval.

Denied ToolInvocations MUST NOT execute. Approval-required ToolInvocations MUST
pause the AgentRun before the guarded side effect occurs. Runtime MUST NOT retry,
rename, reshape, or route a denied ToolInvocation to bypass Policy.

When a ToolInvocation is blocked by approval, `WaitingForApproval` is a waiting
condition on the ToolInvocation or AgentRun status. It is not a separate
AgentRun terminal phase.

A rejected Approval MUST never authorize or execute its ToolInvocation. With
disposition `terminate`, the parent AgentRun becomes terminal. With disposition
`continue`, the ToolInvocation becomes terminal `Denied` with an embedded
ApprovalRejected Observation; the Execution Engine MUST deliver that
Observation exactly once before preparing the next frame.

## Safety

Runtime MUST enforce:

- Workspace isolation.
- Tool operation validation.
- Per-invocation timeout.
- Policy authorization before side effects.

Runtime SHOULD preserve cancellation, redaction, and idempotency metadata when
those values are available. Tool-specific sandbox requirements are defined by
Tool contracts and concrete Tool Runtime specifications.

A ToolInvocation timeout MUST remain visible on the ToolInvocation itself even
if the parent AgentRun later retries or fails.

## Events And Trace

ToolInvocation execution MUST emit events for resource creation, authorization,
denial, approval waiting, start, terminal outcomes, and Observation recording
when those states occur. Implementations SHOULD emit separate requested,
validated, timed out, and cancelled events when those states are represented
separately.

ToolInvocation events MUST include correlation identifier, Workspace, AgentRun,
ToolInvocation, Tool, operation, and runtime or provider actor. Sensitive
arguments and output MUST be redacted according to Policy.

Trace views MUST be able to reconstruct each tool step, including arguments or
redacted argument metadata, policy decision, execution result, Observation
summary, and related Artifacts.
