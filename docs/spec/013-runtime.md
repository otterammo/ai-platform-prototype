# Runtime

## Purpose

Runtime executes scheduled AgentRuns. Runtime is responsible for invoking
Pilots, Models, and Tools, enforcing policy decisions at side-effect boundaries,
recording ToolInvocations and Observations, producing Artifacts, and reporting
execution status.

Runtime is not the control plane.

## Runtime May

Runtime MAY perform the following actions for a scheduled AgentRun:

- Load the admitted AgentRun and related admitted resources.
- Consume Ready Context.
- Invoke the effective Pilot.
- Invoke permitted Models through the Pilot.
- Record, validate, authorize, and execute ToolInvocations.
- Record Observations and return them to the Pilot.
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

## AgentRun Boundary

Runtime MUST treat AgentRun as the unit of execution. Runtime MUST NOT execute a
Mission, Fleet, or Agent directly.

Runtime MUST verify that the AgentRun is scheduled and that required Context is
Ready before execution starts. Runtime SHOULD record start, progress, waiting,
completion, and failure in status and events.

## Context Consumption

Runtime MUST consume Context prepared by the control plane. Runtime MUST NOT
query KnowledgeIndex or Knowledge directly to construct hidden prompt context.

Runtime MAY transform Context into provider prompts according to Pilot strategy.
Such transformation MUST preserve provenance and MUST honor Model limits.

## Tool Invocation

Runtime MUST use the ToolInvocation contract for tool actions. Runtime MUST
validate structured arguments, authorize the ToolInvocation through Policy, pause
for Approval when required, invoke the Tool Runtime only after authorization,
record the Observation, and return the Observation to the Pilot.

Runtime MUST NOT interpret natural-language model output as a tool request.
Runtime MUST NOT execute denied ToolInvocations, and MUST NOT change Tool,
operation, provider, or request shape to bypass denial.

Tool invocation events SHOULD include tool identity, operation, AgentRun
identity, ToolInvocation identity, policy decision, Observation identity, and
correlation data. Sensitive details SHOULD be redacted according to policy.

## Execution Loop

Runtime MUST continue the Pilot execution loop until the Pilot returns a final
response, the AgentRun is cancelled, approval is required, or a termination
limit is reached.

Runtime MUST enforce maximum iteration count, maximum ToolInvocation count,
effective token budget, Tool timeouts, and cancellation. When a limit terminates
execution, runtime MUST record failure or cancellation in AgentRun status and
events.

## Artifact Production

Runtime MUST represent durable outputs as Artifact resources. Artifact creation
SHOULD happen before AgentRun success is reported. Artifact metadata SHOULD link
to AgentRun, Agent, Mission, Workspace, and relevant Context provenance.

## Failure

Runtime MUST report failures through AgentRun status and events. Failure status
SHOULD include reason, message, retryability when known, and related Approval,
Tool, Model, Context, or Artifact references.

Runtime SHOULD avoid process-local-only failure state. A controller or operator
SHOULD be able to understand the failure from resources and events.
