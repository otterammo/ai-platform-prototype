# Resource Model

## Resource Envelope

Every v1 resource MUST use the common resource envelope:

```yaml
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: example
spec: {}
status: {}
```

`apiVersion` MUST identify the API group and version. For v1 resources defined
by this specification, it MUST be `ai.platform/v1`.

`kind` MUST identify the resource kind. Kind names are singular, PascalCase, and
case-sensitive.

`metadata` MUST contain identity and ownership information. `spec` MUST contain
desired state. `status` MUST contain observed state.

Decision and ExecutionFrame are not Resources and MUST NOT use the resource
envelope.

## Metadata

`metadata.name` is required. Names MUST be stable identifiers within the
resource scope. Names SHOULD use lowercase DNS-style identifiers.

`metadata.namespace` identifies the Workspace scope for namespaced resources.
Cluster-scoped resources MUST NOT require a namespace. Workspace-scoped
resources MUST set namespace to the owning Workspace name.

`metadata.labels` MAY hold queryable identifying attributes. Labels MUST NOT be
required for correctness.

`metadata.annotations` MAY hold non-identifying metadata. Annotations MAY be
used for correlation, integration hints, and extension data.

`metadata.ownerReferences` identifies owning resources. At most one owner SHOULD
be marked as the controlling owner for a controller-owned resource.

`metadata.generation` is a monotonically increasing integer representing changes
to desired state. User changes to `spec` MUST advance generation.

## Spec And Status

`spec` is user-owned desired state. Admission MAY default and validate spec
fields before persistence. Controllers MUST NOT use spec to record observed
progress.

`status` is controller-owned observed state. API clients MUST NOT use ordinary
apply or create operations to set status. Status updates MUST be performed
through status-capable control-plane paths.

`status.observedGeneration` records the latest generation a controller has
observed. A resource is current only when the responsible controller has set
`observedGeneration` to the relevant `metadata.generation`.

`status.conditions` is a list of condition objects. A condition MUST include
`type` and `status`, where status is `True`, `False`, or `Unknown`. Conditions
SHOULD include `reason` and `message` when they communicate failure, waiting, or
degraded behavior.

`status.phase` MAY provide a compact lifecycle summary. Phase MUST NOT be the
only machine-readable status when precise conditions are required.

## Reconciliation

The platform uses declarative reconciliation. Controllers compare desired state
with observed state and perform the minimum control-plane changes needed to
converge.

Controllers MUST be idempotent. Controllers SHOULD record failure in status and
events rather than relying on process-local state. Controllers MUST NOT require a
single uninterrupted process to complete reconciliation.

## Scope

Platform, Workspace, Policy, Approval, Model, Tool, Capability, and
FleetTemplate are cluster-scoped unless an extension explicitly defines a
different scope. Mission, Fleet, Agent, AgentRun, ToolInvocation, Artifact,
Knowledge, KnowledgeIndex, and Context are Workspace-scoped. Observation data is
embedded in ToolInvocation status for v1.1 and is not a standalone resource
kind. Decision is a protocol message, not a scoped resource kind.
ExecutionFrame is internal execution state, not a public resource kind in
Platform Specification `v1.3.0`.

Namespaced resources MUST NOT reference resources in another Workspace except
through an explicitly defined cross-scope contract.

## Versioning

Compatible v1 systems MUST preserve the meaning of existing required fields.
New optional fields MAY be added. Removing fields, changing field meaning, or
changing default behavior requires a new version or a formal deprecation path.
