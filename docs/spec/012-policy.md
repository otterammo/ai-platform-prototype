# Policy

## Purpose

Policy governs authorization, approval, and permitted side effects. Policy
applies to control-plane actions, runtime actions, provider use, tool use,
artifact writes, and extension behavior.

Runtime actions that may cause side effects MUST be evaluated by policy before
execution.

## Policy Resource

Policy defines rules over subjects, resources, actions, tools, providers,
Workspaces, Missions, Agents, AgentRuns, or extension-defined attributes.

Policy rules MUST have deterministic evaluation order. A policy decision MUST be
one of allow, deny, or require approval.

If no policy matches an action, the platform MUST define a safe default. The
recommended default for multi-user or shared environments is deny.

## Approval Resource

Approval represents a human or external decision required before a guarded action
continues. Approval MUST include the action identity, requested effect,
requesting resource, current decision state, and decision metadata.

Approval states MUST include Pending, Approved, and Rejected or semantically
equivalent conditions.

## Evaluation

Policy evaluation MUST be deterministic for the same inputs. Evaluation inputs
SHOULD include Workspace, Mission, Fleet, Agent, AgentRun, action type, tool,
provider, requested operation, resource references, actor, and correlation data.
When an action is derived from a Decision, evaluation inputs SHOULD include
Decision type, Decision version, and redacted Decision metadata when available.

Policy evaluation MUST emit events for material decisions.

## Authorization

Allowed actions MAY proceed. Denied actions MUST NOT proceed. Actions requiring
approval MUST pause before the guarded side effect occurs.

Runtime MUST NOT bypass policy by changing Model, Tool, provider, operation
name, or request shape after denial.

## ToolInvocation Authorization

Every ToolInvocation MUST receive a Policy decision before execution. The
decision MUST be allow, deny, or require approval.

Allowed ToolInvocations MAY proceed to execution. Denied ToolInvocations MUST
NOT execute. ToolInvocations that require approval MUST create or reference an
Approval and pause the AgentRun before the guarded side effect occurs.

Policy evaluation for ToolInvocations SHOULD include Tool identity, operation,
arguments or redacted argument metadata, risk level, sandbox requirements,
Workspace, Mission, Fleet, Agent, AgentRun, actor, and correlation data.

## Waiting And Resume

When approval is required, the relevant AgentRun MUST enter a waiting state and
reference the pending Approval. Parent Agent, Fleet, and Mission resources SHOULD
aggregate waiting status.

When Approval is granted, the control plane SHOULD resume eligible AgentRuns.
When Approval is rejected, the AgentRun MUST fail or remain waiting according to
policy.

## Inheritance And Scope

Policy MAY be defined at Platform, Workspace, Mission, Fleet, Agent, or extension
scope. Effective policy MUST define precedence and conflict behavior.

Workspace policy MUST be able to restrict runtime actions within that Workspace.
Platform policy MAY establish global restrictions that Workspace policy cannot
weaken.

## Future RBAC

The platform MAY define role-based access control for API users, controllers,
workers, providers, and plugin actors. RBAC MUST integrate with Policy without
weakening runtime action authorization.
