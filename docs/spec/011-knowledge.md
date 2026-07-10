# Knowledge

## Purpose

Knowledge represents source material available within a Workspace. Knowledge is
not directly consumed by runtime. The platform indexes Knowledge into
KnowledgeIndex resources and assembles Context for AgentRuns.

Runtime MUST consume Context rather than directly querying Knowledge.

## Knowledge

Knowledge resources declare source material, type, reference, relationships, and
metadata. Knowledge references MUST be scoped to the owning Workspace unless a
provider contract explicitly permits another scope.

Knowledge MAY represent documents, requirements, design records, research,
source references, tickets, comments, datasets, or provider-backed external
content.

Knowledge status SHOULD report source availability, freshness, indexing health,
and access failures when known.

## KnowledgeIndex

KnowledgeIndex represents indexed Knowledge for retrieval. It defines selected
sources, index configuration, indexing status, provenance data, and retrieval
readiness.

KnowledgeIndex controllers MUST record whether indexing is Ready, Failed, or
stale. Index status SHOULD include source identities, source hashes or version
markers, chunk counts, and indexing time.

KnowledgeIndex MUST preserve enough provenance to connect retrieved chunks back
to original Knowledge sources.

## Context

Context is the runtime-consumable assembly of retrieved information for an
AgentRun. Context MUST be Workspace-scoped and SHOULD be owned by the AgentRun
that consumes it.

Context SHOULD include query, KnowledgeIndex reference, selected chunks,
source provenance, rendering metadata, and readiness status.

Runtime MUST wait for Context Ready before execution. Runtime MUST NOT mutate
Context while executing an AgentRun.

## Retrieval

Retrieval converts Mission intent, Agent role, and Context query into selected
Knowledge chunks. Retrieval MAY be keyword-based, semantic, graph-based,
provider-backed, or hybrid.

Retrieval MUST respect Workspace boundaries, policy, Knowledge permissions, and
source freshness rules. Retrieval decisions SHOULD be reproducible or
explainable through Context status and events.

## Provenance

Every retrieved Context item SHOULD preserve source reference, document identity,
section or location, chunk identity, content version, and retrieval score when
available.

Artifacts SHOULD be able to reference Context provenance when claiming support
from Knowledge.

## Chunking

Chunking divides Knowledge into retrievable units. Chunking strategy MAY depend
on source type. Chunk identity SHOULD be stable for unchanged source content and
chunking configuration.

Changing chunking configuration SHOULD mark affected KnowledgeIndex resources
stale and cause Context to rebuild before new AgentRuns execute.

## Future Semantic Retrieval

The platform MAY add embeddings, vector indexes, reranking, graph traversal, and
semantic filters. Such extensions MUST preserve Context as the runtime boundary
and MUST preserve provenance for retrieved content.
