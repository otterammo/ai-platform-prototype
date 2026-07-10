# Glossary

## Agent

A Workspace-scoped declarative resource representing a role-bearing participant
in a Fleet. Agent owns Pilot configuration and creates AgentRuns through
controller reconciliation. Agent is not executable.

## AgentRun

A Workspace-scoped executable resource representing one execution attempt by an
Agent. AgentRun is the only executable resource.

## Approval

A resource representing a required decision for a guarded action. Approval
states include Pending, Approved, and Rejected or their semantic equivalents.

## Artifact

A Workspace-scoped resource representing durable output produced by an AgentRun.

## Capability

A cluster-scoped declaration of required abilities, tools, model constraints, or
provider features needed by an Agent role.

## Condition

A structured status entry with type, status, reason, and message fields that
describes a specific observed state.

## Context

A Workspace-scoped resource containing assembled, provenance-bearing information
prepared for an AgentRun. Runtime consumes Context instead of querying Knowledge.

## Control Plane

The platform subsystem responsible for admission, persistence, reconciliation,
scheduling, policy evaluation, status propagation, and events.

## Event

An immutable record of material platform activity associated with resources,
actors, actions, reasons, and correlation data.

## Fleet

A Workspace-scoped declarative resource representing coordinated Agent
composition for one Mission.

## FleetTemplate

A cluster-scoped reusable declaration of Agent composition, capabilities, and
coordination strategy for Fleets.

## Generation

A monotonically increasing metadata value representing desired-state changes to
a resource.

## Knowledge

A Workspace-scoped declaration of source material available for indexing and
Context assembly.

## KnowledgeIndex

A Workspace-scoped resource representing indexed Knowledge, retrieval readiness,
source freshness, chunks, and provenance.

## Mission

A Workspace-scoped declarative resource representing a desired outcome. Mission
is intent, not execution.

## Model

A cluster-scoped resource describing a replaceable model backend, including
provider, capabilities, limits, and configuration.

## ObservedGeneration

A status value recording the latest metadata.generation observed by the
responsible controller.

## Observation

A Workspace-scoped resource containing the structured result of a ToolInvocation
returned to the Pilot and preserved for trace reconstruction.

## OwnerReference

A metadata reference from a child resource to an owning parent resource.
OwnerReferences define lifecycle and status aggregation relationships.

## Pilot

The provider-independent reasoning, prompt, routing, fallback, and model
orchestration configuration owned by an Agent.

## Platform

The top-level installation and global control-plane scope.

## Policy

A resource or rule set governing authorization, approval, and permitted side
effects.

## Runtime

The subsystem responsible for executing scheduled AgentRuns, invoking Pilots,
Models, and Tools, producing Artifacts, and reporting execution status.

## Scheduler

The control-plane component that selects executable AgentRuns for workers.

## Spec

The desired-state section of a resource. Spec is user-owned unless admission
applies documented defaults.

## Status

The observed-state section of a resource. Status is controller-owned.

## Tool

A declared capability provider that runtime may invoke after policy
authorization.

## ToolInvocation

A Workspace-scoped resource representing one structured, policy-governed request
to execute a Tool operation for an AgentRun.

## Tool Runtime

The runtime component or provider adapter that executes authorized
ToolInvocation operations and returns structured output for Observation
recording.

## Workspace

The primary isolation and namespace boundary for Missions, Knowledge, Context,
Fleets, Agents, AgentRuns, ToolInvocations, Observations, and Artifacts.
