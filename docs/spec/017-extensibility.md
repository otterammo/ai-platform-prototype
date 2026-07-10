# Extensibility

## Purpose

The platform is extensible through custom resources, custom controllers,
plugins, tool providers, model providers, knowledge providers, schedulers, and
workers. Extensions MUST preserve core platform contracts.

Extension mechanisms MUST NOT weaken resource ownership, policy, Context
boundaries, event traceability, or AgentRun-only execution.

## Custom Resources

Custom resources MAY extend the platform API. A custom resource SHOULD use the
same resource envelope: apiVersion, kind, metadata, spec, and status.

Custom resources MUST define scope, ownership, spec fields, status fields,
conditions, lifecycle, and compatibility guarantees. If a custom resource affects
execution, it MUST define how it relates to AgentRun.

## Custom Controllers

Custom controllers MAY reconcile core or custom resources. They MUST be
idempotent and level-based. They MUST write observed state to status and emit
events for material decisions.

Custom controllers MUST NOT perform runtime side effects except by creating or
scheduling AgentRuns or by using another explicitly defined control-plane
contract.

## Plugins

Plugins MAY extend CLI behavior, API projections, admission, policy, runtime
capabilities, or provider integrations. Plugins MUST declare their extension
points and required permissions.

Plugins MUST NOT require users to understand hidden state outside resources and
events to reason about platform behavior.

## Tool Providers

Tool providers expose tool operations to runtime. Tool providers MUST identify
operations, input schemas, output schemas, side effects, risk level, timeouts,
retry policy, sandbox requirements, idempotency behavior, redaction behavior,
and policy attributes.

Runtime MUST authorize tool operations before invoking providers. Tool providers
SHOULD emit or return enough metadata for runtime events, Observations, and
Artifacts.

## Model Providers

Model providers expose model backends to Pilots. They MUST declare supported
capabilities, limits, configuration fields, credential requirements, and error
semantics.

Model providers MUST remain replaceable behind Model and Pilot contracts.

## Knowledge Providers

Knowledge providers expose source material for Knowledge and KnowledgeIndex.
They MUST preserve Workspace boundaries, access policy, freshness signals, and
provenance. They SHOULD provide stable source versions or hashes when possible.

## Schedulers And Workers

Custom schedulers MAY select worker classes, priorities, placement, and queues
for AgentRuns. They MUST schedule only AgentRuns.

Custom workers execute scheduled AgentRuns. They MUST report status, honor
policy, consume Context, produce Artifact resources, and emit events.

## Compatibility

Extensions MAY add optional capabilities. They MUST NOT change the meaning of
core v1 fields, core lifecycle semantics, or required policy boundaries. Breaking
extension changes require versioning or deprecation.
