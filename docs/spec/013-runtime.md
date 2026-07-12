# Runtime

## Purpose

Runtime executes scheduled AgentRuns. Runtime includes the Execution Engine,
which is responsible for invoking Pilots, receiving Decisions, interpreting
Decisions into platform actions, invoking Models through Provider Adapters,
invoking Tools through governed interfaces, enforcing policy decisions at
side-effect boundaries, recording ToolInvocations and embedded Observation
data, producing Artifacts, and reporting execution status.

Runtime is not the control plane.

Detailed Execution Engine loop semantics are defined in
[Execution Engine](023-execution-engine.md).

## Runtime May

Runtime MAY perform the following actions for a scheduled AgentRun:

- Load the admitted AgentRun and related admitted resources.
- Consume Ready Context.
- Invoke the effective Pilot.
- Invoke permitted Models through the Pilot and Provider Adapter.
- Validate and interpret Decisions.
- Record, validate, authorize, and execute ToolInvocations.
- Record embedded Observations.
- Enforce execution budgets, timeout, retry, cancellation, and lease ownership.
- Produce Artifacts.
- Update AgentRun status through approved status paths.
- Emit execution events.

## Runtime Must Not

Runtime MUST NOT:

- Schedule work.
- Reconcile resources.
- Create orchestration decisions for Missions, Fleets, or Agents.
- Build Context from Knowledge.
- Perform admission.
- Bypass policy.
- Mutate Mission, Fleet, or Agent spec.
- Claim success before required Artifacts are recorded.
- Treat Decisions as Resources.
- Let Models or Pilots create Resources directly.
- Let Provider Adapters create Resources directly.
- Let provider-specific response formats enter Execution Engine interpretation.
- Infer success from inactivity, empty Model output, or absence of a tool
  request.
- Re-run completed ToolInvocations during retry, resume, or replay.

## AgentRun Boundary

Runtime MUST treat AgentRun as the unit of execution. Runtime MUST NOT execute a
Mission, Fleet, or Agent directly.

Runtime MUST verify that the AgentRun is scheduled and that required Context is
Ready before execution starts. Runtime SHOULD record start, progress, waiting,
completion, and failure in status and events.

Runtime MUST execute the AgentRun generation identified by
`status.observedGeneration` and MUST NOT advance a stale generation as if it were
current.

## Execution Engine

Execution Engine is the runtime component that owns AgentRun control flow. It
MUST validate Decisions, interpret Decisions, create ToolInvocations when
appropriate, enforce iteration limits, enforce timeouts, handle cancellation,
apply retry policy, deliver Observations to later iterations, and determine
terminal AgentRun state.

Execution Engine MUST be the only component that converts Decisions into
platform actions. It MUST NOT bypass Resource admission, Policy, Approval,
Workspace isolation, status ownership, Events, or trace contracts.

Execution Engine MUST resume from persisted AgentRun, Decision-frame, and
ToolInvocation state. It MUST NOT use replay to repeat external side effects.
Events are audit records; they are not the operational source of truth for the
next side effect.

One AgentRun may have only one active Execution Engine owner. Runtime MUST stop
initiating new side effects when it loses the AgentRun lease, epoch, or fencing
token.

## Context Consumption

Runtime MUST consume Context prepared by the control plane. Runtime MUST NOT
query KnowledgeIndex or Knowledge directly to construct hidden prompt context.

Runtime MAY transform Context into provider prompts according to Pilot strategy
and Provider Adapter requirements. Such transformation MUST preserve provenance
and MUST honor Model limits.

## Decisions

Runtime MUST treat Decision as the provider-neutral protocol between Model
output and platform action. Decisions are ephemeral and MUST NOT be persisted as
Resources.

Provider-specific output MUST be normalized through the Model Protocol before
Execution Engine interpretation. Runtime MUST NOT treat provider-native tool
calls, structured-output payloads, refusal payloads, JSON envelopes, streaming
chunks, or local decoder internals as Decisions.

Runtime MUST emit enough status or event data to reconstruct Decision order,
Provider Adapter metadata, validation, rejection, interpretation, retries, and
resulting platform actions in trace.

Runtime MUST preserve at most one active Decision per AgentRun. Completion and
failure MUST be represented by explicit `complete` or `fail` Decisions or by a
structured runtime terminal reason; natural-language text is not completion.

## Tool Invocation

Runtime MUST use the ToolInvocation contract for tool actions. Runtime MUST
validate structured arguments, authorize the ToolInvocation through Policy, pause
for Approval when required, invoke the Tool Runtime only after authorization,
and record the embedded Observation.

An `invoke_tool` Decision becomes a ToolInvocation only after Execution Engine
validation and interpretation.

Each `invoke_tool` Decision creates exactly one deterministic ToolInvocation.
Runtime MUST NOT create duplicate ToolInvocations after reconciliation or crash
recovery.

Runtime MUST NOT execute denied ToolInvocations, and MUST NOT change Tool,
operation, provider, or request shape to bypass denial.

Tool invocation events SHOULD include tool identity, operation, AgentRun
identity, ToolInvocation identity, policy decision, Observation summary or error
metadata, and correlation data. Sensitive details SHOULD be redacted according
to policy.

## Artifact Production

Runtime MUST represent durable outputs as Artifact resources. Artifact creation
SHOULD happen before AgentRun success is reported. Artifact metadata SHOULD link
to AgentRun, Agent, Mission, Workspace, and relevant Context provenance.

## Failure

Runtime MUST report failures through AgentRun status and events. Failure status
SHOULD include reason, message, retryability when known, and related Approval,
Tool, Model, Provider Adapter, Context, or Artifact references.

Runtime SHOULD avoid process-local-only failure state. A controller or operator
SHOULD be able to understand the failure from resources and events.

Runtime MUST distinguish `Failed`, `Cancelled`, `TimedOut`, and
`BudgetExceeded` terminal states. Timeout and budget exhaustion MUST NOT be
reported as generic failure.
