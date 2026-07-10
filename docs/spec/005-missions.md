# Missions

## Purpose

Mission represents a desired outcome in a Workspace. It is the primary user
intent resource. A Mission MUST describe what should be accomplished and SHOULD
define success criteria, inputs, and expected outputs.

Mission MUST represent desired outcomes rather than execution. It MUST NOT
represent a job attempt, queue item, model invocation, tool call, or worker task.

## Scope And Ownership

Mission is Workspace-scoped. A Mission MUST belong to exactly one Workspace and
MUST own the Fleet created to satisfy it.

A Mission MAY reference Workspace Knowledge through declared inputs. A Mission
MAY reference a FleetTemplate to select an execution topology. A Mission MAY
declare output expectations that guide Fleet and Agent behavior.

## Desired State

Mission spec SHOULD include:

- Intent: the outcome to achieve.
- Success criteria: observable conditions for completion.
- Inputs: references to Knowledge, parameters, or external resources admitted
  by policy.
- Outputs: expected artifact classes or deliverables.
- Template selection: a FleetTemplate or selection constraints.

Mission spec MUST be stable enough for repeated reconciliation. Reapplying the
same Mission spec SHOULD NOT create a new execution attempt unless generation or
explicit retry policy requires it.

## Template Selection

If a Mission names a FleetTemplate, the control plane MUST use that template
when creating or updating the Mission Fleet. If a Mission supplies template
selection criteria rather than a name, the selecting controller MUST record the
selected template in status or child resource state.

If no suitable FleetTemplate exists, the Mission MUST become Failed or blocked
with a condition explaining the missing template.

## Lifecycle

Mission lifecycle is driven by Fleet status aggregation:

- Pending: admitted but not yet reconciled.
- Reconciling: child resources are being created or updated.
- Running: at least one AgentRun is active or scheduled.
- Waiting: progress requires approval or another external decision.
- Completed: success criteria have been satisfied.
- Failed: the Mission cannot complete under current desired state.

Implementations MAY use additional phases, but MUST preserve equivalent
conditions for waiting, completed, and failed states.

## Status Propagation

Mission status MUST reflect the aggregate state of its owned Fleet. Mission
status SHOULD include references to the active Fleet, current AgentRuns, pending
Approvals, produced Artifacts, and failure causes.

Mission status MUST set observedGeneration after the Mission controller has
observed the corresponding generation and reconciled or rejected it.

## Events

Mission events MUST be emitted for admission, Fleet creation or update, template
selection, waiting, completion, failure, and generation drift that causes
reconciliation.
