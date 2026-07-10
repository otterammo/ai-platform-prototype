# RFC-0001: Structured Tool Invocation And Execution Loop

## Title

Structured Tool Invocation And Execution Loop.

## Authors

TBD.

## Status

Draft.

## Motivation

The platform can execute Missions with language models and produce Markdown
Artifacts, but that flow cannot inspect repositories, create or modify files,
execute commands, iterate after observing tool output, or complete software
engineering workflows.

Agents may declare Tools and Policy may authorize their use, but runtime needs a
first-class contract for requesting, authorizing, executing, observing, and
tracing tool operations.

## Background

Current execution is:

```text
Mission
-> Context
-> Model
-> Artifact
```

The proposed execution path is:

```text
Mission
-> Context
-> AgentRun
-> Pilot
-> Model
-> ToolInvocation
-> Policy
-> Tool Runtime
-> Observation
-> Pilot
-> Model
-> ...
-> Artifact
```

The loop continues until the Pilot determines that the Mission is complete or a
configured termination condition is reached.

## Goals

- Introduce first-class ToolInvocation resources.
- Define structured model-directed tool calls.
- Define an iterative execution loop.
- Return structured Observations to the Pilot.
- Govern every tool execution through Policy.
- Preserve a complete execution trace.

The platform should support workloads such as feature implementation, bug fixes,
code review, documentation updates, dependency upgrades, and repository
maintenance without redesigning the architecture.

## Non-Goals

This RFC does not introduce autonomous planning across multiple Missions,
distributed workers, browser automation, GUI automation, or arbitrary plugin
loading.

## Proposed Design

Runtime invokes the effective Pilot for an AgentRun. The Pilot asks the selected
Model for the next decision and receives one of two structured responses:

- `FinalResponse`
- `ToolInvocationRequest`

Runtime MUST NOT infer tool operations from natural language. Tool requests MUST
be structured and machine-readable.

When the Pilot requests a tool operation, runtime creates or records a
ToolInvocation, validates its arguments against the Tool contract, evaluates
Policy, executes the authorized tool operation through a Tool Runtime, records an
Observation, and returns that Observation to the Pilot as execution context. The
loop repeats until a final response or a termination condition.

## Resource Changes

ToolInvocation is a Workspace-scoped resource owned by an AgentRun. It records
the tool, operation, arguments, policy decision, execution phase, and result for
one requested tool operation. ToolInvocation resources are immutable after
completion.

Observation is a Workspace-scoped resource owned by an AgentRun and linked to a
ToolInvocation. It records the structured result that is returned to the Pilot,
including summary, payload, error details, and output references when allowed by
Policy.

Tool contracts MUST define operations, input schema, output schema, risk level,
timeout, retry policy, sandbox requirements, side effects, idempotency, and
redaction behavior. Runtime MUST validate ToolInvocation arguments before
execution.

## API Changes

Compatible APIs SHOULD expose ToolInvocation and Observation resources through
the normal resource endpoints. Watch and trace projections SHOULD include tool
execution progress, policy decisions, Observations, and terminal results.

## Controller Changes

Controllers continue to reconcile declarative resources and schedule AgentRuns.
Controllers MUST NOT execute tools. Status aggregation SHOULD surface waiting,
denied, failed, timed out, and completed tool activity through AgentRun, Agent,
Fleet, and Mission status.

## Runtime Changes

Runtime becomes an iterative execution engine for AgentRuns. Runtime
responsibilities include invoking Pilot, validating ToolInvocations, enforcing
Policy, executing Tool Runtimes, capturing Observations, emitting Events,
continuing the loop, honoring termination limits, and producing Artifacts.

Runtime MUST NOT perform planning for Missions, Fleets, or Agents.

## Built-In Tool Runtimes

Initial built-in Tool Runtimes should include:

- Filesystem: `read`, `write`, `append`, `list`, and `mkdir`, restricted to the
  Workspace root.
- Git: `status`, `diff`, `add`, `commit`, and `branch`, with no push support
  initially.
- Shell: approved command execution with stdout, stderr, exit code, sandboxing,
  and configurable timeout.

## Safety

The platform must enforce Workspace isolation, path traversal protection,
command timeout, maximum iteration count, maximum tool invocation count, maximum
token budget, and cancellation support. Infinite execution loops must terminate
predictably.

## Event And Trace Changes

The event taxonomy should include:

- ToolInvocationRequested
- ToolInvocationValidated
- ToolInvocationAuthorized
- ToolInvocationDenied
- ToolInvocationStarted
- ToolInvocationCompleted
- ToolInvocationFailed
- ObservationRecorded
- PilotContinued
- PilotCompleted

Tool execution events MUST include correlation data that identifies the
Workspace, AgentRun, ToolInvocation, and responsible runtime or provider actor.
Trace views should reconstruct each execution step from ToolInvocation,
Observation, Policy, AgentRun, Artifact, and Event records.

## Migration Strategy

The RFC introduces new resource kinds and runtime behavior without changing the
meaning of existing v1 fields. Existing Mission, Fleet, Agent, AgentRun, Context,
Policy, and Artifact behavior remains valid. Implementations may add the new
resources behind capability gates before enabling tool-executing AgentRuns.

No implementation should begin until the Platform Specification incorporates the
concepts defined in this RFC.

## Alternatives Considered

Natural-language tool requests were rejected because they are difficult to
validate, authorize, replay, and audit.

Embedding tool results only in logs was rejected because logs are not a
first-class resource contract and cannot reliably drive Pilot continuation or
trace reconstruction.

## Risks

- Poorly bounded loops can consume unbounded time, tokens, or tool executions.
- Tool providers may leak sensitive arguments or output unless redaction is part
  of the contract.
- Shell and filesystem tools can damage workspaces if sandbox and traversal
  controls are incomplete.
- Retrying side-effecting operations can duplicate changes unless idempotency is
  explicit.

## Open Questions

- Which ToolInvocation phases should be required in the stable resource schema?
- What storage compaction rules are allowed for Observations without weakening
  the resource API, ordering, or trace reconstruction contract?
- Which built-in shell commands should be allowed by default?
- What approval resume semantics are required for long-running AgentRuns?
