# Architecture

## Overview

The platform is organized as a hierarchy of declarative resources. Parent
resources own intent and scope. Child resources refine that intent until a single
executable unit, the AgentRun, can be scheduled and executed.

```text
Platform
`-- Workspace
    `-- Mission
        `-- Fleet
            `-- Agent
                `-- AgentRun
                    |-- Context
                    |-- Pilot
                    |   `-- Model
                    |-- ToolInvocation
                    |   `-- Observation
                    `-- Artifact
```

## Resource Responsibilities

Platform represents the top-level installation and global control-plane scope.
It owns Workspace resources and platform-wide capabilities.

Workspace represents a project, tenant, or collaboration boundary. It defines
resource isolation, knowledge boundaries, security boundaries, and ownership
scope for namespaced resources.

Mission represents a desired outcome. A Mission MUST describe intent and success
criteria. It MUST NOT represent a process, task queue entry, or execution
attempt.

Fleet represents the coordinated plan selected to satisfy a Mission. A Fleet MAY
be derived from a FleetTemplate. It owns Agent composition and aggregates Agent
status.

Agent represents a role-bearing participant in a Fleet. An Agent declares
capabilities, tools, memory relationship, and Pilot configuration. An Agent MUST
remain independent of any specific model provider.

AgentRun represents one execution attempt for an Agent. AgentRun is the only
executable resource in the platform.

Context represents assembled, provenance-bearing information prepared for an
AgentRun. Runtime MUST consume Context and MUST NOT query Knowledge directly.

Pilot represents the agent reasoning and model orchestration policy. Model is a
replaceable execution backend selected or routed by Pilot.

Artifact represents durable output produced by an AgentRun.

ToolInvocation represents one structured, governed request to execute a Tool
operation during an AgentRun. Observation represents the structured result of
that invocation returned to the Pilot.

## Ownership

Every namespaced workload resource MUST have a clear owner chain rooted at a
Workspace. Controller-owned children MUST use ownerReferences to identify their
controlling parent. A child resource MUST NOT outlive the ownership boundary of
its parent unless a retention policy explicitly says otherwise.

Ownership determines lifecycle, garbage collection, status aggregation, and
traceability. Ownership MUST NOT be used as an authorization substitute; policy
and admission remain responsible for authorization.

## Lifecycle

Resources move through lifecycle by reconciliation. Users and API clients write
desired state to `spec`. Controllers observe resources, create or update child
resources, and write observed state to `status`. Runtimes observe scheduled
AgentRuns, execute them, and report status and artifacts.

Controllers MUST be level-based. They SHOULD converge from current observed
state to desired state and SHOULD tolerate repeated reconciliation of the same
resource.

## Boundaries

The control plane owns admission, persistence, reconciliation, scheduling,
policy evaluation, status propagation, and events. Runtime owns AgentRun
execution, Pilot invocation, model invocation, structured tool invocation,
Observation recording, and artifact production.

Runtime MUST NOT schedule work, reconcile resources, perform admission, build
Context, or make orchestration decisions. Controllers MUST NOT perform runtime
side effects except through explicitly scheduled AgentRuns.
