# ADR 0007: Embedded Observations In v1.1

## Title

Embedded Observations In v1.1

## Status

Accepted

## Context

RFC-0001 originally described Observation as a standalone Workspace-scoped
resource. The v1.1 implementation needs traceable tool results, but it does not
yet need an independent Observation lifecycle, garbage collection policy,
admission path, or reconciliation contract.

## Decision

For Platform Specification v1.1, Observation is structured data embedded in
`ToolInvocation.status.observation`, not a standalone resource kind. CLI, API,
event, and trace surfaces may project embedded Observation data for inspection,
but ToolInvocation remains the persisted resource identity.

Future RFCs may introduce Observation as a standalone resource if independent
lifecycle, retention, streaming, or cross-resource reference requirements
justify that complexity. Such a change must preserve v1.1 traceability and
compatibility expectations.

## Consequences

The initial ToolInvocation framework has one durable resource lifecycle instead
of two, which keeps ownership and status aggregation compact. Trace consumers
can still reconstruct tool execution from ToolInvocation status and
ObservationRecorded events.

Large outputs must use scoped output references or future retention rules rather
than unbounded embedded payloads. A later standalone Observation resource would
need a migration or projection strategy for v1.1 embedded observations.

## Alternatives

Creating a standalone Observation resource in v1.1 was rejected because the
current implementation does not require separate admission, ownership, or
retention semantics.

Storing observations only in events was rejected because current tool result
state must remain available on the ToolInvocation status surface.
