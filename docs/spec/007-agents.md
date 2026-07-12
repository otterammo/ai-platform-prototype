# Agents

## Purpose

Agent represents a role-bearing participant in a Fleet. An Agent declares the
capabilities, tools, memory relationship, and Pilot configuration needed to
perform work for a Mission.

Agent is declarative. Agent MUST NOT execute work directly. AgentRun is the only
executable resource.

## Scope And Ownership

Agent is Workspace-scoped. An Agent MUST be owned by exactly one Fleet in the
same Workspace. Agent identity MUST be stable for the life of the Fleet
generation that created it.

Agent status MUST aggregate the current or most recent owned AgentRun.

## Identity

Agent identity consists of Workspace, Fleet, name, and role. Role describes the
Agent's responsibility within the Fleet, such as planner, researcher, reviewer,
builder, or executor.

Role names are platform vocabulary. Implementations and extensions SHOULD reuse
canonical role names when possible and SHOULD avoid synonyms that obscure status
aggregation.

## Memory

Agent memory is platform-managed context and history associated with an Agent's
role and Mission. Agent memory MUST NOT bypass Workspace boundaries or policy.

Agent memory MAY be represented by Context, Artifacts, Events, or extension
resources. Runtime MAY consume memory only through resources admitted for the
AgentRun.

## Tools

Agent spec MAY declare required tools or capabilities that imply tools. Tools
MUST be resolved before execution. Runtime actions involving tools MUST be
authorized by policy before side effects occur.

Agent MUST NOT assume a tool provider is available merely because a tool name is
present. Tool availability is a control-plane and provider contract.

Resolved tools MUST provide operation schemas, risk metadata, sandbox
requirements, and output contracts before runtime can execute ToolInvocations.

## Pilot Ownership

Agent owns Pilot configuration. Pilot configuration defines reasoning strategy,
prompt strategy, model routing, fallback, and future multi-model orchestration.

Agent MUST remain independent of any specific model provider. If Agent references
a Model, the reference MUST be expressed through Pilot or a Pilot-compatible
routing contract.

Agent configuration MAY provide default AgentRun execution budgets. Runtime
enforces the effective budget, and Pilot or Model MUST NOT alter it.

## AgentRun Creation

Agent controllers create AgentRuns. Each AgentRun is an execution attempt for
the Agent. The Agent controller SHOULD create a new AgentRun when Agent desired
state changes or retry policy requires a new attempt.

Runtime executes AgentRuns, not Agents. Agent status MUST be derived from
AgentRun status and related Context, Approval, and Artifact state.

## Events

Agent events MUST be emitted for creation, update, AgentRun creation, status
aggregation, waiting, completion, and failure.
