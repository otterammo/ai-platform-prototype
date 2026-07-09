from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .events import EventContext
from .resources import (
    ContextResource,
    KnowledgeIndexResource,
    KnowledgeRef,
    KnowledgeResource,
    MissionResource,
    ResourceKind,
    WorkspaceResource,
    parse_resource,
)
from .storage import ResourceStore

JsonDict = dict[str, Any]
JsonDictList = list[JsonDict]

DEFAULT_INDEX_NAME = "default"
DEFAULT_CONTEXT_LIMIT = 10
MAX_CHUNK_CHARS = 1200


@dataclass(frozen=True)
class KnowledgeDocument:
    ref: str
    path: Path
    document: str
    content: str
    content_hash: str


@dataclass(frozen=True)
class MarkdownChunk:
    section: str
    content: str


@dataclass(frozen=True)
class ContextBuildResult:
    context: JsonDict
    chunks: JsonDictList
    sources: JsonDictList
    rendered_context: str
    retrieval_time_ms: float


class KnowledgeStore:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    def load(self, workspace: WorkspaceResource, knowledge_ref: KnowledgeRef) -> KnowledgeDocument:
        path = self.resolve(workspace, knowledge_ref)
        if path.suffix.lower() != ".md":
            raise ValueError(f"knowledge source {knowledge_ref.ref} must be a markdown file")
        content = path.read_text(encoding="utf-8")
        return KnowledgeDocument(
            ref=knowledge_ref.ref,
            path=path,
            document=knowledge_ref.path,
            content=content,
            content_hash=_sha256(content),
        )

    def resolve(self, workspace: WorkspaceResource, knowledge_ref: KnowledgeRef) -> Path:
        workspace_root = workspace.spec.resolved_root(self.store.platform_root, workspace.metadata.name)
        knowledge_root = (workspace_root / "knowledge").resolve(strict=False)
        candidate = knowledge_root / Path(knowledge_ref.path)
        resolved_candidate = candidate.resolve(strict=False)
        try:
            resolved_candidate.relative_to(knowledge_root)
        except ValueError as exc:
            raise PermissionError(
                f"knowledge reference {knowledge_ref.ref} resolves outside workspace knowledge"
            ) from exc
        if not resolved_candidate.is_file():
            raise FileNotFoundError(f"knowledge reference {knowledge_ref.ref} not found at {candidate}")
        return resolved_candidate


class MarkdownChunker:
    def __init__(self, max_chars: int = MAX_CHUNK_CHARS) -> None:
        self.max_chars = max_chars

    def chunk(self, document: KnowledgeDocument) -> list[MarkdownChunk]:
        sections = self._sections(document.content)
        chunks: list[MarkdownChunk] = []
        for section, text in sections:
            for content in self._chunk_section(text):
                if content:
                    chunks.append(MarkdownChunk(section=section, content=content))
        if not chunks and document.content.strip():
            chunks.append(MarkdownChunk(section="", content=document.content.strip()))
        return chunks

    def _sections(self, content: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        headings: list[str] = []
        current_lines: list[str] = []
        current_section = ""
        for line in content.splitlines():
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if match:
                if current_lines:
                    sections.append((current_section, "\n".join(current_lines).strip()))
                level = len(match.group(1))
                title = match.group(2).strip().strip("#").strip()
                headings = headings[: level - 1]
                headings.append(title)
                current_section = " > ".join(headings)
                current_lines = [line]
                continue
            current_lines.append(line)
        if current_lines:
            sections.append((current_section, "\n".join(current_lines).strip()))
        return sections

    def _chunk_section(self, content: str) -> list[str]:
        paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", content) if paragraph.strip()]
        chunks: list[str] = []
        current: list[str] = []
        current_length = 0
        for paragraph in paragraphs:
            projected = current_length + len(paragraph) + (2 if current else 0)
            if current and projected > self.max_chars:
                chunks.append("\n\n".join(current))
                current = [paragraph]
                current_length = len(paragraph)
                continue
            current.append(paragraph)
            current_length = projected
        if current:
            chunks.append("\n\n".join(current))
        return chunks


class KnowledgeIndexer:
    def __init__(
        self,
        store: ResourceStore,
        knowledge_store: KnowledgeStore | None = None,
        chunker: MarkdownChunker | None = None,
    ) -> None:
        self.store = store
        self.knowledge_store = knowledge_store or KnowledgeStore(store)
        self.chunker = chunker or MarkdownChunker()

    def ensure_indexed(
        self,
        namespace: str,
        index_name: str = DEFAULT_INDEX_NAME,
        extra_sources: list[KnowledgeRef] | None = None,
        correlation_id: str | None = None,
    ) -> JsonDict:
        workspace = self._workspace(namespace)
        sources = self._ensure_index_resource(namespace, index_name, extra_sources or [], correlation_id)
        documents = [self.knowledge_store.load(workspace, source) for source in sources]
        source_hashes = _source_hashes(documents)
        manifest = self.store.get(ResourceKind.KNOWLEDGE_INDEX, index_name, namespace)
        chunks = self.store.list_knowledge_chunks(namespace, index_name)
        if (
            manifest
            and (manifest.get("status") or {}).get("phase") == "Ready"
            and (manifest.get("status") or {}).get("data", {}).get("sourceHashes") == source_hashes
            and (chunks or not documents)
        ):
            return manifest
        return self.index(namespace, index_name, sources=sources, documents=documents, correlation_id=correlation_id)

    def index(
        self,
        namespace: str,
        index_name: str = DEFAULT_INDEX_NAME,
        sources: list[KnowledgeRef] | None = None,
        documents: list[KnowledgeDocument] | None = None,
        correlation_id: str | None = None,
    ) -> JsonDict:
        workspace = self._workspace(namespace)
        desired_sources = sources or self._ensure_index_resource(namespace, index_name, [], correlation_id)
        self._ensure_index_resource(namespace, index_name, desired_sources, correlation_id)
        try:
            loaded_documents = documents or [self.knowledge_store.load(workspace, source) for source in desired_sources]
            chunk_records = self._chunk_records(index_name, loaded_documents)
            stored_chunks = self.store.replace_knowledge_chunks(namespace, index_name, chunk_records)
            source_hashes = _source_hashes(loaded_documents)
            now = datetime.now(UTC).isoformat()
            for chunk in stored_chunks:
                self.store.emit_event(
                    "ChunkCreated",
                    ResourceKind.KNOWLEDGE_INDEX,
                    index_name,
                    namespace,
                    f"Created chunk {chunk['chunkId']} for KnowledgeIndex {index_name}",
                    {
                        "knowledgeIndex": index_name,
                        "sourceRef": chunk["sourceRef"],
                        "document": chunk["document"],
                        "section": chunk["section"],
                        "chunkId": chunk["chunkId"],
                        "chunkCount": len(stored_chunks),
                    },
                    event_context=self._context(namespace, "CreateChunk", "ChunkCreated", correlation_id),
                )
            return self.store.update_status(
                ResourceKind.KNOWLEDGE_INDEX,
                index_name,
                namespace,
                "Ready",
                f"KnowledgeIndex {index_name} indexed {len(stored_chunks)} chunks",
                {
                    "knowledgeIndex": index_name,
                    "sourceCount": len(loaded_documents),
                    "chunkCount": len(stored_chunks),
                    "indexedAt": now,
                    "sourceHashes": source_hashes,
                },
                event_type="KnowledgeIndexed",
                event_context=self._context(namespace, "IndexKnowledge", "KnowledgeIndexed", correlation_id),
            )
        except Exception as exc:
            self.store.update_status(
                ResourceKind.KNOWLEDGE_INDEX,
                index_name,
                namespace,
                "Failed",
                str(exc),
                {"error": str(exc)},
                event_type="KnowledgeIndexFailed",
                event_context=self._context(namespace, "IndexKnowledge", "IndexFailed", correlation_id),
            )
            raise

    def _chunk_records(self, index_name: str, documents: list[KnowledgeDocument]) -> JsonDictList:
        records: JsonDictList = []
        for source_order, document in enumerate(documents):
            for chunk_index, chunk in enumerate(self.chunker.chunk(document)):
                content_hash = _sha256(chunk.content)
                records.append(
                    {
                        "sourceRef": document.ref,
                        "document": document.document,
                        "sourceOrder": source_order,
                        "sourceHash": document.content_hash,
                        "section": chunk.section,
                        "chunkId": f"{index_name}-{source_order}-{chunk_index}-{content_hash[:12]}",
                        "chunkIndex": chunk_index,
                        "content": chunk.content,
                        "contentHash": content_hash,
                        "metadata": {
                            "path": str(document.path),
                            "sourceRef": document.ref,
                            "document": document.document,
                            "section": chunk.section,
                        },
                    }
                )
        return records

    def _ensure_index_resource(
        self,
        namespace: str,
        index_name: str,
        extra_sources: list[KnowledgeRef],
        correlation_id: str | None,
    ) -> list[KnowledgeRef]:
        existing_manifest = self.store.get(ResourceKind.KNOWLEDGE_INDEX, index_name, namespace)
        sources = self._sources_from_manifest(existing_manifest)
        if not sources:
            sources = self._sources_from_knowledge_resources(namespace)
        sources = _dedupe_sources([*sources, *extra_sources])
        desired_refs = [source.ref for source in sources]
        existing_refs = [source.ref for source in self._sources_from_manifest(existing_manifest)]
        if existing_manifest is None or existing_refs != desired_refs:
            self.store.apply(
                {
                    "apiVersion": "ai.platform/v1",
                    "kind": "KnowledgeIndex",
                    "metadata": {"name": index_name, "namespace": namespace},
                    "spec": {"sources": [{"ref": source.ref} for source in sources]},
                },
                event_context=self._context(
                    namespace,
                    "ApplyKnowledgeIndex",
                    "KnowledgeIndexConfigured",
                    correlation_id,
                ),
            )
        return sources

    def _workspace(self, namespace: str) -> WorkspaceResource:
        manifest = self.store.get(ResourceKind.WORKSPACE, namespace)
        if manifest is None:
            raise KeyError(f"Workspace {namespace} not found")
        resource = parse_resource(manifest)
        if not isinstance(resource, WorkspaceResource):
            raise TypeError(f"expected WorkspaceResource, got {type(resource).__name__}")
        return resource

    def _sources_from_manifest(self, manifest: JsonDict | None) -> list[KnowledgeRef]:
        if manifest is None:
            return []
        resource = parse_resource(manifest)
        if not isinstance(resource, KnowledgeIndexResource):
            raise TypeError(f"expected KnowledgeIndexResource, got {type(resource).__name__}")
        return list(resource.spec.sources)

    def _sources_from_knowledge_resources(self, namespace: str) -> list[KnowledgeRef]:
        sources: list[KnowledgeRef] = []
        for manifest in self.store.list(ResourceKind.KNOWLEDGE, namespace):
            resource = parse_resource(manifest)
            if isinstance(resource, KnowledgeResource):
                sources.append(resource.spec.ref)
        return _dedupe_sources(sources)

    @staticmethod
    def _context(namespace: str, action: str, reason: str, correlation_id: str | None) -> EventContext:
        return EventContext(
            controller="KnowledgeIndexer",
            action=action,
            reason=reason,
            correlation_id=correlation_id,
            workspace=namespace,
        )


class KeywordRetriever:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    def retrieve(
        self,
        namespace: str,
        index_name: str,
        query: str,
        *,
        limit: int = DEFAULT_CONTEXT_LIMIT,
        preferred_refs: list[str] | None = None,
    ) -> JsonDictList:
        preferred = set(preferred_refs or [])
        terms = _query_terms(query)
        results: JsonDictList = []
        for chunk in self.store.list_knowledge_chunks(namespace, index_name):
            score, matched_terms = self._score(chunk, terms)
            if chunk["sourceRef"] in preferred:
                score += 1.0
            if score <= 0:
                continue
            scored = dict(chunk)
            scored["score"] = score
            scored["matchedTerms"] = matched_terms
            results.append(scored)
        results.sort(key=lambda item: (-float(item["score"]), int(item["sourceOrder"]), int(item["chunkIndex"])))
        return results[:limit]

    @staticmethod
    def _score(chunk: JsonDict, terms: list[str]) -> tuple[float, list[str]]:
        haystack = " ".join(
            [
                str(chunk.get("document") or ""),
                str(chunk.get("section") or ""),
                str(chunk.get("content") or ""),
            ]
        ).lower()
        score = 0.0
        matched_terms: list[str] = []
        for term in terms:
            count = haystack.count(term)
            if count:
                score += float(count)
                matched_terms.append(term)
        return score, matched_terms


class ContextBuilder:
    def __init__(
        self,
        store: ResourceStore,
        indexer: KnowledgeIndexer | None = None,
        retriever: KeywordRetriever | None = None,
    ) -> None:
        self.store = store
        self.indexer = indexer or KnowledgeIndexer(store)
        self.retriever = retriever or KeywordRetriever(store)

    def build_for_mission(
        self,
        mission: MissionResource,
        *,
        index_name: str = DEFAULT_INDEX_NAME,
        preferred_sources: list[KnowledgeRef] | None = None,
        limit: int = DEFAULT_CONTEXT_LIMIT,
        correlation_id: str | None = None,
    ) -> ContextBuildResult:
        namespace = mission.metadata.namespace
        if namespace is None:
            raise ValueError("Mission must have a namespace")
        query = mission_query(mission)
        preferred = preferred_sources or mission_knowledge_refs(mission)
        self.indexer.ensure_indexed(namespace, index_name, preferred, correlation_id=correlation_id)
        self.store.emit_event(
            "RetrievalStarted",
            ResourceKind.CONTEXT,
            mission.metadata.name,
            namespace,
            f"Started retrieval for Mission {mission.metadata.name}",
            {"knowledgeIndex": index_name, "query": query},
            event_context=self._context(
                namespace,
                mission.metadata.name,
                "RetrieveContext",
                "RetrievalStarted",
                correlation_id,
            ),
        )
        started = time.perf_counter()
        retrieved = self.retriever.retrieve(
            namespace,
            index_name,
            query,
            limit=limit,
            preferred_refs=[source.ref for source in preferred],
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        unique = _dedupe_chunks(retrieved)
        ordered = sorted(unique, key=lambda item: (int(item["sourceOrder"]), int(item["chunkIndex"])))
        sources = _source_summaries(ordered)
        rendered_context = render_context(sources, ordered)
        self.store.emit_event(
            "RetrievalCompleted",
            ResourceKind.CONTEXT,
            mission.metadata.name,
            namespace,
            f"Retrieved {len(ordered)} chunks for Mission {mission.metadata.name}",
            {
                "knowledgeIndex": index_name,
                "chunkCount": len(ordered),
                "retrievalTimeMs": round(elapsed_ms, 3),
                "sources": sources,
            },
            event_context=self._context(
                namespace,
                mission.metadata.name,
                "RetrieveContext",
                "RetrievalCompleted",
                correlation_id,
            ),
        )
        context = self._persist_context(
            mission,
            index_name,
            query,
            sources,
            ordered,
            rendered_context,
            correlation_id,
        )
        return ContextBuildResult(
            context=context,
            chunks=ordered,
            sources=sources,
            rendered_context=rendered_context,
            retrieval_time_ms=elapsed_ms,
        )

    def _persist_context(
        self,
        mission: MissionResource,
        index_name: str,
        query: str,
        sources: JsonDictList,
        chunks: JsonDictList,
        rendered_context: str,
        correlation_id: str | None,
    ) -> JsonDict:
        namespace = mission.metadata.namespace
        if namespace is None:
            raise ValueError("Mission must have a namespace")
        self.store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Context",
                "metadata": {"name": mission.metadata.name, "namespace": namespace},
                "spec": {
                    "mission": mission.metadata.name,
                    "query": query,
                    "knowledgeIndex": index_name,
                },
            },
            event_context=self._context(
                namespace,
                mission.metadata.name,
                "ApplyContext",
                "ContextConfigured",
                correlation_id,
            ),
        )
        return self.store.update_status(
            ResourceKind.CONTEXT,
            mission.metadata.name,
            namespace,
            "Ready",
            f"Context built with {len(chunks)} chunks",
            {
                "sources": sources,
                "chunks": [_context_chunk(chunk) for chunk in chunks],
                "renderedContext": rendered_context,
                "chunkCount": len(chunks),
                "knowledgeIndex": index_name,
                "builtAt": datetime.now(UTC).isoformat(),
            },
            event_type="ContextBuilt",
            event_context=self._context(
                namespace,
                mission.metadata.name,
                "BuildContext",
                "ContextBuilt",
                correlation_id,
            ),
        )

    @staticmethod
    def _context(
        namespace: str,
        mission: str,
        action: str,
        reason: str,
        correlation_id: str | None,
    ) -> EventContext:
        return EventContext(
            controller="ContextBuilder",
            action=action,
            reason=reason,
            correlation_id=correlation_id,
            workspace=namespace,
            mission=mission,
        )


def mission_query(mission: MissionResource) -> str:
    parts = [mission.spec.objective, mission.metadata.name, mission.spec.template]
    outputs = [name for name, enabled in mission.spec.outputs.items() if enabled]
    parts.extend(outputs)
    return " ".join(part for part in parts if part)


def mission_knowledge_refs(mission: MissionResource) -> list[KnowledgeRef]:
    refs: list[KnowledgeRef] = []
    if mission.spec.brief:
        refs.append(mission.spec.brief)
    refs.extend(mission.spec.inputs.values())
    return _dedupe_sources(refs)


def render_context(sources: JsonDictList, chunks: JsonDictList) -> str:
    lines = ["Context", "Sources"]
    if sources:
        for source in sources:
            lines.append(f"- {source['document']} ({source['chunkCount']} chunks)")
    else:
        lines.append("- none")
    lines.append("Chunks")
    if not chunks:
        lines.append("No matching knowledge chunks were retrieved.")
        return "\n".join(lines)
    for index, chunk in enumerate(chunks, start=1):
        section = f"#{chunk['section']}" if chunk.get("section") else ""
        lines.append(f"{index}. {chunk['document']}{section}")
        lines.append(f"Source: {chunk['sourceRef']} | Chunk: {chunk['chunkId']}")
        lines.append(str(chunk["content"]).strip())
    return "\n".join(lines)


def _source_hashes(documents: list[KnowledgeDocument]) -> JsonDictList:
    return [{"ref": document.ref, "hash": document.content_hash} for document in documents]


def _query_terms(query: str) -> list[str]:
    terms = re.findall(r"[a-z0-9]+", query.lower())
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        unique.append(term)
    return unique


def _dedupe_sources(sources: list[KnowledgeRef]) -> list[KnowledgeRef]:
    seen: set[str] = set()
    unique: list[KnowledgeRef] = []
    for source in sources:
        if source.ref in seen:
            continue
        seen.add(source.ref)
        unique.append(source)
    return unique


def _dedupe_chunks(chunks: JsonDictList) -> JsonDictList:
    seen: set[str] = set()
    unique: JsonDictList = []
    for chunk in chunks:
        content_hash = str(chunk["contentHash"])
        if content_hash in seen:
            continue
        seen.add(content_hash)
        unique.append(chunk)
    return unique


def _source_summaries(chunks: JsonDictList) -> JsonDictList:
    summaries: dict[str, JsonDict] = {}
    for chunk in chunks:
        source_ref = str(chunk["sourceRef"])
        if source_ref not in summaries:
            summaries[source_ref] = {
                "sourceRef": source_ref,
                "document": chunk["document"],
                "chunkCount": 0,
                "sections": [],
            }
        summaries[source_ref]["chunkCount"] += 1
        section = chunk.get("section")
        if section and section not in summaries[source_ref]["sections"]:
            summaries[source_ref]["sections"].append(section)
    return list(summaries.values())


def _context_chunk(chunk: JsonDict) -> JsonDict:
    return {
        "sourceRef": chunk["sourceRef"],
        "document": chunk["document"],
        "section": chunk["section"],
        "chunkId": chunk["chunkId"],
        "contentHash": chunk["contentHash"],
        "score": chunk.get("score", 0),
        "matchedTerms": chunk.get("matchedTerms", []),
        "content": chunk["content"],
    }


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_context_manifest(manifest: JsonDict | None) -> ContextResource | None:
    if manifest is None:
        return None
    resource = parse_resource(manifest)
    if isinstance(resource, ContextResource):
        return resource
    return None
