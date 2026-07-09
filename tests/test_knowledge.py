from __future__ import annotations

from pathlib import Path

import pytest

from ai_platform.knowledge import (
    ContextBuilder,
    KeywordRetriever,
    KnowledgeDocument,
    KnowledgeIndexer,
    MarkdownChunker,
)
from ai_platform.resources import ResourceKind, parse_resource
from ai_platform.storage import ResourceStore


def make_knowledge_store(tmp_path: Path, files: dict[str, str]) -> tuple[ResourceStore, Path]:
    workspace_root = tmp_path / "workspace"
    knowledge_dir = workspace_root / "knowledge"
    knowledge_dir.mkdir(parents=True)
    for name, content in files.items():
        path = knowledge_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Workspace",
            "metadata": {"name": "demo"},
            "spec": {"rootPath": str(workspace_root)},
        }
    )
    return store, workspace_root


def apply_index(store: ResourceStore, sources: list[str]) -> None:
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "KnowledgeIndex",
            "metadata": {"name": "default", "namespace": "demo"},
            "spec": {"sources": sources},
        }
    )


def apply_execution_parent_chain(store: ResourceStore) -> str:
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Fleet",
            "metadata": {"name": "build-auth-fleet", "namespace": "demo"},
            "spec": {
                "workspace": "demo",
                "mission": "build-auth",
                "strategy": "single-agent",
                "agentCount": 1,
            },
        }
    )
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Agent",
            "metadata": {"name": "build-auth-agent", "namespace": "demo"},
            "spec": {
                "workspace": "demo",
                "mission": "build-auth",
                "fleet": "build-auth-fleet",
            },
        }
    )
    run_name = "build-auth-agent-run-1"
    store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "AgentRun",
            "metadata": {"name": run_name, "namespace": "demo"},
            "spec": {
                "agentRef": {"name": "build-auth-agent"},
                "missionRef": {"name": "build-auth"},
                "contextRef": {"name": f"{run_name}-context"},
            },
        }
    )
    return run_name


def test_markdown_chunker_splits_sections_deterministically(tmp_path: Path) -> None:
    document = KnowledgeDocument(
        ref="knowledge://prd.md",
        path=tmp_path / "prd.md",
        document="prd.md",
        content="# PRD\n\nShip authentication.\n\n## Details\n\nUse sessions.\n\nUse passwords.",
        content_hash="hash",
    )

    chunks = MarkdownChunker(max_chars=35).chunk(document)

    assert [chunk.section for chunk in chunks] == ["PRD", "PRD > Details", "PRD > Details"]
    assert chunks[0].content == "# PRD\n\nShip authentication."
    assert chunks[1].content == "## Details\n\nUse sessions."
    assert chunks[2].content == "Use passwords."


def test_indexer_persists_chunks_status_and_events(tmp_path: Path) -> None:
    store, _workspace_root = make_knowledge_store(
        tmp_path,
        {
            "prd.md": "# PRD\n\nShip authentication.",
            "architecture.md": "# Architecture\n\nUse sessions.",
        },
    )
    apply_index(store, ["knowledge://prd.md", "knowledge://architecture.md"])

    index = KnowledgeIndexer(store).index("demo")

    assert index["status"]["phase"] == "Ready"
    assert index["status"]["data"]["sourceCount"] == 2
    assert index["status"]["data"]["chunkCount"] == 2
    chunks = store.list_knowledge_chunks("demo", "default")
    assert [chunk["document"] for chunk in chunks] == ["prd.md", "architecture.md"]
    assert chunks[0]["section"] == "PRD"
    event_types = {event["type"] for event in store.list_events(namespace="demo", limit=20)}
    assert {"ChunkCreated", "KnowledgeIndexed"}.issubset(event_types)


def test_keyword_retrieval_scores_matches_and_preferred_sources(tmp_path: Path) -> None:
    store, _workspace_root = make_knowledge_store(
        tmp_path,
        {
            "prd.md": "# PRD\n\nAuthentication must support password login.",
            "research.md": "# Research\n\nBilling language only.",
        },
    )
    apply_index(store, ["knowledge://prd.md", "knowledge://research.md"])
    KnowledgeIndexer(store).index("demo")

    results = KeywordRetriever(store).retrieve(
        "demo",
        "default",
        "authentication",
        preferred_refs=["knowledge://research.md"],
    )

    assert [result["document"] for result in results] == ["prd.md", "research.md"]
    assert results[0]["matchedTerms"] == ["authentication"]
    assert results[1]["score"] == 1.0


def test_context_builder_deduplicates_chunks_and_records_provenance(tmp_path: Path) -> None:
    store, _workspace_root = make_knowledge_store(
        tmp_path,
        {
            "prd.md": "# PRD\n\nAuthentication requires MFA.",
            "copy.md": "# PRD\n\nAuthentication requires MFA.",
            "architecture.md": "# Architecture\n\nAuthentication uses sessions.",
        },
    )
    apply_index(store, ["knowledge://prd.md", "knowledge://copy.md", "knowledge://architecture.md"])
    mission_manifest = store.apply(
        {
            "apiVersion": "ai.platform/v1",
            "kind": "Mission",
            "metadata": {"name": "build-auth", "namespace": "demo"},
            "spec": {"objective": "Build authentication"},
        }
    )
    mission = parse_resource(mission_manifest)
    run_name = apply_execution_parent_chain(store)

    result = ContextBuilder(store).build_for_mission(
        mission,
        context_name=f"{run_name}-context",
        agent_run=run_name,
    )

    assert len(result.chunks) == 2
    assert [chunk["document"] for chunk in result.chunks] == ["prd.md", "architecture.md"]
    assert result.sources == [
        {"sourceRef": "knowledge://prd.md", "document": "prd.md", "chunkCount": 1, "sections": ["PRD"]},
        {
            "sourceRef": "knowledge://architecture.md",
            "document": "architecture.md",
            "chunkCount": 1,
            "sections": ["Architecture"],
        },
    ]
    assert "Context\nSources" in result.rendered_context
    assert "Source: knowledge://prd.md" in result.rendered_context
    context = store.get(ResourceKind.CONTEXT, f"{run_name}-context", "demo")
    assert context is not None
    assert context["metadata"]["ownerReferences"] == [{"kind": "AgentRun", "name": run_name, "controller": True}]
    assert context["spec"]["agentRun"] == run_name
    assert context["status"]["phase"] == "Ready"
    assert context["status"]["data"]["chunkCount"] == 2


def test_ensure_indexed_rebuilds_stale_index(tmp_path: Path) -> None:
    store, workspace_root = make_knowledge_store(tmp_path, {"prd.md": "# PRD\n\nAuthentication."})
    apply_index(store, ["knowledge://prd.md"])
    indexer = KnowledgeIndexer(store)
    indexer.index("demo")
    old_hash = store.get(ResourceKind.KNOWLEDGE_INDEX, "default", "demo")["status"]["data"]["sourceHashes"][0]["hash"]

    (workspace_root / "knowledge" / "prd.md").write_text("# PRD\n\nAuthentication and sessions.", encoding="utf-8")
    indexer.ensure_indexed("demo")

    index = store.get(ResourceKind.KNOWLEDGE_INDEX, "default", "demo")
    assert index is not None
    assert index["status"]["data"]["sourceHashes"][0]["hash"] != old_hash
    results = KeywordRetriever(store).retrieve("demo", "default", "sessions")
    assert results[0]["document"] == "prd.md"


def test_indexer_rejects_non_markdown_sources(tmp_path: Path) -> None:
    store, _workspace_root = make_knowledge_store(tmp_path, {"notes.txt": "plain text"})
    apply_index(store, ["knowledge://notes.txt"])

    with pytest.raises(ValueError, match="markdown"):
        KnowledgeIndexer(store).index("demo")

    index = store.get(ResourceKind.KNOWLEDGE_INDEX, "default", "demo")
    assert index is not None
    assert index["status"]["phase"] == "Failed"
