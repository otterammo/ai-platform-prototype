# ADR 0003: Knowledge Index Architecture

## Title

Knowledge Index Architecture

## Status

Accepted

## Context

AI work depends on source material, freshness, retrieval, and provenance. If
runtime reads knowledge sources directly, the platform cannot reliably trace
which information influenced an AgentRun or enforce consistent Workspace
boundaries.

## Decision

Knowledge is modeled as first-class resources. KnowledgeIndex resources manage
indexed source material, and Context resources hold assembled,
provenance-bearing information prepared for AgentRuns. Runtime consumes Context
instead of querying Knowledge directly.

## Consequences

Knowledge freshness, source identity, chunks, and provenance become part of the
platform contract. Controllers own index and context assembly. Runtime behavior
is easier to audit because AgentRuns reference prepared Context.

## Alternatives

Direct file reads from runtime were rejected because they hide provenance and
freshness. Provider-specific retrieval inside model calls was rejected because
it bypasses Workspace boundaries and weakens traceability.
