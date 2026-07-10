# Workspaces

## Purpose

Workspace is the primary isolation boundary for platform work. A Workspace
represents a project, tenant, team, repository, product area, or other bounded
context in which Missions, Knowledge, Context, Fleets, Agents, AgentRuns, and
Artifacts are created.

Workspace MUST define the namespace for Workspace-scoped resources.

## Scope

Workspace is cluster-scoped. A Workspace owns namespaced resources whose
`metadata.namespace` equals the Workspace name.

Resources in one Workspace MUST NOT read, mutate, or depend on resources in
another Workspace unless a cross-Workspace API explicitly permits that behavior.

## Ownership Boundary

Workspace ownership establishes lifecycle boundaries. Deleting a Workspace MAY
delete child resources, mark them terminating, or retain them according to a
declared retention policy. The default behavior SHOULD prevent orphaned
execution resources.

Workspace ownership MUST NOT imply that all actors with access to one resource
have access to all resources in the Workspace. Authorization remains a policy
decision.

## Knowledge Boundary

Workspace is the default boundary for Knowledge and KnowledgeIndex. Knowledge
references MUST resolve within the owning Workspace unless an explicit provider
contract authorizes external retrieval.

Runtime MUST consume Context assembled for the same Workspace as the AgentRun.
Runtime MUST NOT directly query Workspace Knowledge.

## Security Boundary

Workspace MUST be treated as a security boundary for policy, credentials,
runtime configuration, artifacts, and knowledge access. Workspace-scoped
runtime actions MUST carry Workspace identity into policy evaluation and events.

Provider credentials MAY be inherited from Platform scope, but use of those
credentials MUST be authorized in the Workspace context before side effects
occur.

## Resource Isolation

Names of Workspace-scoped resources are unique within a Workspace and kind.
Controllers MUST resolve namespaced references relative to the owning Workspace
unless a field explicitly states a different scope.

Workspace status SHOULD summarize readiness, policy posture, knowledge health,
and degraded child resources when those signals are available.

## Lifecycle

A Workspace is Ready when it has passed admission, is persisted, and its required
platform services are available. A Workspace SHOULD become Degraded when
required policy, knowledge, runtime, or provider dependencies are unavailable.

Workspace lifecycle events MUST include creation, update, deletion or
termination, readiness changes, and material isolation failures.
