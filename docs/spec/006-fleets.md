# Fleets

## Purpose

Fleet represents the coordinated agent composition selected to satisfy a
Mission. It is a declarative orchestration resource, not an executable resource.

Fleet bridges Mission intent and Agent identity. A Fleet owns the Agents that
will participate in satisfying its Mission and aggregates their status.

## Scope And Ownership

Fleet is Workspace-scoped. A Fleet MUST be owned by exactly one Mission in the
same Workspace. A Fleet MUST NOT be shared across Missions.

Fleet owns Agent resources. Agent resources created for a Fleet MUST reference
that Fleet as their controlling owner.

## FleetTemplate

FleetTemplate defines reusable agent composition. A Fleet MAY be derived from a
FleetTemplate. When a Fleet is derived from a FleetTemplate, the Fleet MUST carry
enough desired state or reference data for controllers to detect template drift.

A FleetTemplate SHOULD describe:

- Agent names or roles.
- Required capabilities.
- Coordination strategy.
- Expected aggregation behavior.
- Optional constraints for model, tool, or runtime placement.

FleetTemplate is cluster-scoped by default. Template use in a Workspace MAY be
restricted by policy.

## Agent Composition

Fleet spec MUST define the desired Agent set either directly or through a
FleetTemplate. Each desired Agent MUST have a stable identity within the Fleet.

Controllers MUST resolve required capabilities before creating or updating
Agents. If capability resolution fails, the Fleet MUST surface the failure and
SHOULD avoid partially applying an inconsistent Agent set.

## Execution Coordination

Fleet MAY define coordination strategy, including sequential, parallel,
role-based, or dependency-based execution. Regardless of strategy, Fleet MUST
coordinate by creating and observing Agents and AgentRuns. Fleet MUST NOT
execute work directly.

Fleet coordination decisions MUST be observable through child resources, status,
and events.

## Aggregation

Fleet status MUST aggregate owned Agent state. If any required Agent fails, the
Fleet SHOULD fail unless strategy defines a tolerated failure mode. If any Agent
is waiting for approval, the Fleet SHOULD report Waiting. If all required Agents
complete successfully, the Fleet SHOULD report Succeeded.

Fleet status SHOULD include Agent references, pending Approvals, produced
Artifacts, and failure causes.

## Events

Fleet events MUST be emitted for template resolution, capability resolution,
Agent creation or update, waiting, completion, and failure.
