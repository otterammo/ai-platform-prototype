# Versioning

The AI Platform uses separate versioning for the Platform Specification,
resource APIs, and implementation packages.

## Platform Specification

The Platform Specification uses semantic versioning:

- `MAJOR` changes introduce incompatible architectural or behavioral contracts.
- `MINOR` changes add backward-compatible resources, fields, behaviors, or
  guarantees.
- `PATCH` changes clarify language, fix examples, or correct non-contractual
  errors without changing behavior.

The current Platform Specification version is `v1.0.0`.

Specification versions are independent from the Python package version in
`pyproject.toml`. An implementation package may release multiple versions while
claiming compatibility with the same Platform Specification version.

## Resource APIs

Resource API versions are encoded in `apiVersion`.

Examples:

- `ai.platform/v1`
- `ai.platform/v1beta1`
- `ai.platform/v2alpha1`

Stable versions such as `ai.platform/v1` MUST preserve the meaning of existing
required fields and lifecycle semantics. Compatible changes MAY add optional
fields, status details, event types, or new resource kinds when existing
behavior remains valid.

Beta versions such as `ai.platform/v1beta1` are intended for broad testing.
They SHOULD be documented, migration-aware, and close to stable, but they MAY
change before promotion to stable.

Alpha versions such as `ai.platform/v2alpha1` are experimental. They MAY change
or be removed, but they MUST NOT silently change stable API behavior.

## Compatibility Expectations

Compatible implementations MUST preserve:

- resource identity by API version, kind, name, and scope
- desired-state ownership of `spec`
- controller ownership of `status`
- generation and observedGeneration semantics
- ownerReferences and resource lifecycle boundaries
- AgentRun-only execution
- policy enforcement before side effects
- Context consumption by runtime
- event and traceability guarantees

Breaking changes require a new major Platform Specification version or a new
resource API version. Deprecations SHOULD include migration guidance, a removal
target, and compatibility notes in the relevant RFC.
