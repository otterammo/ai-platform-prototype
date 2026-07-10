# RFC-0001: Tool Invocation Framework

## Title

Tool Invocation Framework.

## Authors

TBD.

## Status

Draft.

## Motivation

The platform can execute Missions with language models and produce Markdown
Artifacts, but it lacks a first-class execution boundary for governed tool
operations. Agents may declare Tools and Policy may authorize their use, but
runtime needs a durable contract for requesting, validating, authorizing,
executing, observing, and tracing one tool operation.

Without that framework, later agentic runtime work would have to rely on logs,
natural-language conventions, or provider-specific behavior. That would weaken
policy, replay, audit, retries, and traceability.

## Background

Current execution can produce an Artifact from Context and a Model, but tool use
is not represented as platform state. RFC-0001 introduces the resource and
runtime contracts needed before any iterative agent loop or built-in tool
runtime is implemented.

The framework path is:

```text
AgentRun
-> ToolInvocation
-> Policy
-> Tool Runtime
-> Observation
```

This RFC defines one governed tool operation. It does not define how a Pilot or
Model decides to request that operation, nor does it define a multi-turn loop.

## Goals

- Introduce first-class ToolInvocation resources.
- Introduce first-class Observation resources.
- Define Tool operation contracts.
- Define the runtime interface for executing one ToolInvocation.
- Ensure every ToolInvocation flows through Policy.
- Preserve events and trace data for each tool operation.

## Non-Goals

This RFC does not introduce:

- Pilot execution-loop semantics.
- Model-to-Pilot structured output protocol.
- Built-in filesystem, git, or shell Tool Runtime implementations.
- Multi-turn agent execution.
- Autonomous planning across multiple Missions.
- Distributed workers.
- Browser automation, GUI automation, or arbitrary plugin loading.

Those concerns are split into later RFCs.

## Proposed Design

Runtime executes tools only through ToolInvocation resources. A ToolInvocation
records the requested Tool, operation, arguments, correlation data, policy
decision, execution phase, and terminal result for one operation.

Runtime validates a ToolInvocation against the referenced Tool contract, asks
Policy for an authorization decision, invokes the Tool Runtime only when
authorized or approved, and records an Observation with the structured result.

Observation is the durable resource that captures the result of a
ToolInvocation. It can be consumed by trace views, API clients, future Pilot
continuation logic, and any implementation that needs to reason about tool
output without relying on process-local logs.

## Resource Changes

ToolInvocation is a Workspace-scoped resource owned by an AgentRun.

ToolInvocation spec records:

- AgentRun reference.
- Tool identity or Tool reference.
- Operation name.
- Structured arguments.
- Correlation identifier.
- Idempotency key for side-effecting operations when one can be derived.

ToolInvocation status records:

- Phase.
- Policy decision or Approval reference.
- Tool Runtime identity when available.
- Start and completion timestamps when available.
- Terminal result metadata.
- Error reason and message when applicable.

ToolInvocation spec is immutable after creation. A completed ToolInvocation is
terminal; corrections must be represented by a later event, Observation, or
replacement ToolInvocation.

Observation is a Workspace-scoped resource owned by the same AgentRun and linked
to exactly one ToolInvocation.

Observation status records:

- Summary.
- Structured payload matching the Tool operation output schema when execution
  succeeds.
- Error details when execution fails, is denied, times out, or is cancelled.
- Redaction metadata when Policy withholds arguments or output.
- Output references for large payloads.

## Tool Contract

Every executable Tool definition must define:

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

Runtime must validate ToolInvocation arguments before policy authorization and
execution.

## API Changes

Compatible APIs should expose ToolInvocation and Observation resources through
the normal resource endpoints. Watch and trace projections should include tool
execution progress, policy decisions, Observations, and terminal results.

## Controller Changes

Controllers continue to reconcile declarative resources and schedule AgentRuns.
Controllers must not execute tools. Status aggregation should surface waiting,
denied, failed, timed out, cancelled, and completed tool activity through
AgentRun, Agent, Fleet, and Mission status.

## Runtime Changes

Runtime becomes responsible for executing one admitted ToolInvocation at a time:

1. Load the AgentRun and ToolInvocation.
2. Validate ToolInvocation arguments against the Tool contract.
3. Evaluate Policy.
4. Pause before execution when approval is required.
5. Invoke the Tool Runtime only after authorization.
6. Record the Observation.
7. Update ToolInvocation and AgentRun status.
8. Emit events.

Runtime must not infer ToolInvocations from natural-language model output. The
protocol for model-directed decisions is deferred to RFC-0004.

## Safety

The framework must enforce Workspace isolation, Tool operation validation,
per-invocation timeouts, cancellation, redaction, idempotency metadata, and
policy authorization before side effects.

Tool-specific sandbox rules are defined by Tool contracts and by later RFCs that
introduce concrete Tool Runtime implementations.

## Event And Trace Changes

The event taxonomy should include:

- ToolInvocationRequested
- ToolInvocationValidated
- ToolInvocationAuthorized
- ToolInvocationDenied
- ToolInvocationWaitingForApproval
- ToolInvocationStarted
- ToolInvocationCompleted
- ToolInvocationFailed
- ToolInvocationTimedOut
- ToolInvocationCancelled
- ObservationRecorded

Tool execution events must include correlation data that identifies the
Workspace, AgentRun, ToolInvocation, Tool, operation, and responsible runtime or
provider actor.

Trace views should reconstruct each tool step from ToolInvocation, Observation,
Policy, AgentRun, Artifact, and Event records.

## Migration Strategy

The RFC introduces new resource kinds and runtime behavior without changing the
meaning of existing v1 fields. Existing Mission, Fleet, Agent, AgentRun,
Context, Policy, and Artifact behavior remains valid.

Implementations may add ToolInvocation and Observation resources behind
capability gates before enabling any tool-executing AgentRuns.

## Follow-On RFCs

- RFC-0002 defines Pilot execution-loop semantics.
- RFC-0003 defines built-in filesystem, git, and shell Tool Runtimes.
- RFC-0004 defines the structured Pilot-to-Model protocol.
- RFC-0005 composes the framework, protocol, loop, and built-in tools into a
  multi-turn Agent Runtime.

## Alternatives Considered

Embedding tool results only in logs was rejected because logs are not a
first-class resource contract and cannot reliably drive audit, replay, status,
or trace reconstruction.

Provider-specific tool-call formats were rejected as the platform framework
because they would make ToolInvocation semantics depend on a replaceable Model
provider.

## Risks

- Tool providers may leak sensitive arguments or output unless redaction is part
  of the contract.
- Retrying side-effecting operations can duplicate changes unless idempotency is
  explicit.
- ToolInvocation resources may grow large unless Observation payload and output
  reference rules are clear.

## Open Questions

- Which ToolInvocation phases should be required in the stable resource schema?
- What storage compaction rules are allowed for Observations without weakening
  the resource API, ordering, or trace reconstruction contract?
- How much of Tool Runtime identity should be persisted for audit without
  leaking provider internals?
