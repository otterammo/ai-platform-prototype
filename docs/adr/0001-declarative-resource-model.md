# ADR 0001: Declarative Resource Model

## Title

Declarative Resource Model

## Status

Accepted

## Context

The platform needs a durable contract for AI work that can be reconciled,
observed, versioned, and implemented by different control planes. Imperative
task APIs would couple users to a specific execution path and make replay,
ownership, and traceability harder.

## Decision

The platform represents intent and observed state as declarative resources with
`apiVersion`, `kind`, `metadata`, `spec`, and `status`. Users and clients write
desired state to `spec`; controllers and runtime report observed state through
`status`, events, and artifacts.

## Consequences

All significant platform capabilities need resource contracts or explicit
extension points. Controllers must reconcile level-based desired state rather
than depend on one-shot commands. Implementation details remain subordinate to
the Platform Specification.

## Alternatives

An imperative job API was rejected because it would make ownership, drift
detection, replay, and extension harder to reason about. A provider-specific
workflow model was rejected because it would weaken model and runtime
replaceability.
