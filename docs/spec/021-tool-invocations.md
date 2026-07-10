# Tool Invocations

## Purpose

ToolInvocation represents one structured request to execute a Tool operation for
an AgentRun. It is the runtime-side effect boundary where model-directed intent
becomes a governed platform action.

Observation represents the structured result of a ToolInvocation that is
returned to the Pilot and preserved for trace reconstruction.

Runtime MUST NOT execute tools from natural-language instructions. Tool
operations MUST be represented as structured ToolInvocation requests before any
side effect occurs.

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
- Summary suitable for Pilot continuation.
- Structured payload matching the Tool operation output schema when execution
  succeeds.
- Error reason and message when execution fails, is denied, times out, or is
  cancelled.
- Redaction metadata when arguments or outputs were withheld by policy.

An Observation SHOULD include output references for large data rather than
embedding unbounded payloads. Shell Observations SHOULD preserve exit code,
stdout reference or excerpt, stderr reference or excerpt, and timeout state when
allowed by policy.

Runtime MUST make Observations available to the Pilot in execution order.
Runtime MUST preserve enough Observation data in resources or API projections for
trace reconstruction.

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

## Execution Loop

Pilot decisions MUST use a structured contract. A Pilot decision MUST be either
a final response or a ToolInvocation request.

For each ToolInvocation request, runtime MUST:

1. Record or derive a ToolInvocation identity.
2. Validate arguments against the Tool contract.
3. Evaluate Policy.
4. Pause for Approval when Policy requires approval.
5. Execute the authorized Tool operation through the selected Tool Runtime.
6. Record the Observation.
7. Return the Observation to the Pilot.
8. Continue the AgentRun loop until a final response or termination condition.

Runtime MUST NOT perform Mission, Fleet, or Agent planning while running this
loop.

## Built-In Tool Runtimes

Compatible implementations MAY provide built-in Tool Runtimes. When provided,
the following operation contracts apply.

Filesystem runtime operations are `read`, `write`, `append`, `list`, and
`mkdir`. Filesystem operations MUST be restricted to the Workspace root and MUST
reject path traversal outside that root.

Git runtime operations are `status`, `diff`, `add`, `commit`, and `branch`. Git
runtime MUST NOT push to remote repositories unless a later specification
explicitly adds a push contract.

Shell runtime operation is approved command execution. Shell execution MUST
capture stdout, stderr, exit code, and timeout state. Shell execution MUST remain
sandboxed and MUST use the effective timeout from Tool, Policy, Pilot, or
AgentRun configuration.

## Policy

Every ToolInvocation MUST pass through Policy before execution. A Policy
decision MUST be allow, deny, or require approval.

Denied ToolInvocations MUST NOT execute. Approval-required ToolInvocations MUST
pause the AgentRun before the guarded side effect occurs. Runtime MUST NOT retry,
rename, reshape, or route a denied ToolInvocation to bypass Policy.

## Safety And Termination

Runtime MUST enforce:

- Workspace isolation.
- Path traversal protection for filesystem-backed operations.
- Command timeout for shell-backed operations.
- Maximum Pilot iteration count.
- Maximum ToolInvocation count per AgentRun.
- Maximum effective token budget.
- Cancellation support.

If a termination limit is reached, runtime MUST stop the execution loop, record
AgentRun failure or cancellation status, and emit events that identify the
triggering limit.

## Events And Trace

ToolInvocation execution MUST emit events for requested, validated, authorized,
denied, approval waiting, started, completed, failed, timed out, cancelled, and
Observation recorded states when those states occur.

ToolInvocation events MUST include correlation identifier, Workspace, AgentRun,
ToolInvocation, Tool, operation, and runtime or provider actor. Sensitive
arguments and output MUST be redacted according to Policy.

Trace views MUST be able to reconstruct each tool step, including arguments or
redacted argument metadata, policy decision, execution result, Observation
summary, related Artifacts, and continuation or completion of the Pilot loop.
