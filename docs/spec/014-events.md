# Events

## Purpose

Events are immutable records of material platform activity. They provide
traceability, debugging context, audit evidence, replay support, and lifecycle
history for resources.

Events MUST NOT replace resource status. Status represents current observed
state. Events represent historical facts.

## Immutability

Once recorded, an event MUST NOT be mutated. Correction SHOULD be represented by
a later event. Event stores MAY apply retention policies, but retention MUST NOT
change resource semantics.

## Correlation

Events SHOULD include correlation identifiers that connect Platform, Workspace,
Mission, Fleet, Agent, AgentRun, Context, Approval, Model, Tool,
ToolInvocation, Observation, and Artifact activity for the same unit of work.

Correlation identifiers MUST be stable across controller, scheduler, runtime,
and provider boundaries when propagated.

## Ordering

Events MUST include creation time or a monotonic sequence sufficient for
ordering within a platform installation. Distributed implementations SHOULD
document ordering guarantees and clock-skew behavior.

Consumers MUST NOT assume total ordering across independent Workspaces unless
the platform explicitly provides it.

## Traceability

Events SHOULD include:

- Event type.
- Resource kind, namespace, and name.
- Controller, scheduler, runtime, worker, or provider actor.
- Action and reason.
- Human-readable message.
- Structured payload.
- Correlation data.

Events SHOULD be specific enough for a reader to reconstruct why a resource
changed phase or condition.

## Replay

Events MAY support replay, projections, timelines, and traces. Replay consumers
MUST treat events as historical facts and MUST reconcile them with current
resource state before making control decisions.

The control plane MUST NOT depend solely on event replay for correctness unless
event replay is part of its documented persistence contract.

## Resource Snapshots

Events MAY include resource snapshots or partial snapshots. Snapshots SHOULD be
used for auditability when resources change generation, status, ownership, or
critical references.

Snapshots MUST respect redaction and policy constraints.

## Taxonomy

The event taxonomy SHOULD include resource applied, reconciliation started,
reconciliation completed, admission rejected, template selected, capability
resolved, model selected, context built, AgentRun scheduled, AgentRun started,
policy evaluated, approval requested, approval granted, approval rejected, model
invoked, ToolInvocation requested, ToolInvocation validated, ToolInvocation
authorized, ToolInvocation denied, ToolInvocation waiting for approval,
ToolInvocation started, ToolInvocation completed, ToolInvocation failed,
ToolInvocation timed out, ToolInvocation cancelled, Observation recorded,
artifact ready, completed, waiting, and failed.

ToolInvocation events MUST include correlation identifier, Workspace, AgentRun,
ToolInvocation, Tool, operation, and runtime or provider actor. Sensitive
arguments and output MUST be redacted according to Policy.

## Trace Semantics

Trace projections MUST be able to reconstruct the execution path for a Mission
or AgentRun from resources and events. For tool-executing AgentRuns, trace MUST
show each ToolInvocation, policy decision, execution phase, result, Observation,
and related Artifacts.

Trace output MUST distinguish missing data from redacted data. Redaction MUST be
driven by Policy and MUST NOT remove the fact that a ToolInvocation occurred.

Extensions MAY add event types. Extension event types SHOULD use stable names
and SHOULD include the same correlation fields as core events.
