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
                    |-- ToolInvocation
                    `-- Artifact
```

Execution uses protocol boundaries that are not all resources:

```text
AgentRun
-> Execution Engine
-> Pilot
-> Model
-> Decision
-> Execution Engine
-> Platform Resources
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

Execution Engine represents the runtime control-flow component for an AgentRun.
It owns Decision validation, Decision interpretation, iteration, retry,
timeout, cancellation, ToolInvocation creation, Observation handling, and
terminal state.

Context represents assembled, provenance-bearing information prepared for an
AgentRun. Runtime MUST consume Context and MUST NOT query Knowledge directly.

Pilot represents the stateless agent reasoning and model orchestration policy.
Pilot owns prompt construction, model selection, provider adaptation, response
parsing, and Decision production. Model is a replaceable execution backend
selected or routed by Pilot.

Decision represents provider-neutral Model intent returned through Pilot and
interpreted by the Execution Engine. Decision is not a Resource.

Artifact represents durable output produced by an AgentRun.

ToolInvocation represents one structured, governed request to execute a Tool
operation during an AgentRun. Observation data is embedded in ToolInvocation
status for status, API projections, and trace reconstruction.

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
execution through the Execution Engine, Pilot invocation, model invocation,
Decision validation and interpretation, structured tool invocation, embedded
Observation recording, and artifact production.

Runtime MUST NOT schedule work, reconcile resources, perform admission, build
Context, or make orchestration decisions. Controllers MUST NOT perform runtime
side effects except through explicitly scheduled AgentRuns.
