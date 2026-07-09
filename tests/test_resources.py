from __future__ import annotations

import pytest

from ai_platform.resources import ResourceKind, parse_resource_documents
from ai_platform.storage import ResourceStore


def test_parse_resource_documents_validates_hierarchy() -> None:
    resources = parse_resource_documents(
        """
apiVersion: ai.platform/v1
kind: Workspace
metadata:
  name: demo
---
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: build-auth
  namespace: demo
spec:
  objective: Build authentication
  brief:
    ref: knowledge://prd.md
"""
    )

    assert [resource.kind for resource in resources] == ["Workspace", "Mission"]
    assert resources[1].metadata.namespace == "demo"


def test_parse_platform_owner_references_agent_run_and_artifact() -> None:
    resources = parse_resource_documents(
        """
apiVersion: ai.platform/v1
kind: Platform
metadata:
  name: local
spec:
  mode: local
---
apiVersion: ai.platform/v1
kind: AgentRun
metadata:
  name: coder-run-1
  workspace: demo
  ownerReferences:
    - kind: Agent
      name: coder
      controller: true
spec:
  agentRef:
    name: coder
  missionRef:
    name: implement-auth
  contextRef:
    name: implement-auth-context
---
apiVersion: ai.platform/v1
kind: Artifact
metadata:
  name: coder-output
  namespace: demo
  ownerReferences:
    - kind: AgentRun
      name: coder-run-1
      controller: true
spec:
  type: markdown
  path: artifacts/coder-output.md
  producedBy:
    kind: AgentRun
    name: coder-run-1
"""
    )

    assert [resource.kind for resource in resources] == ["Platform", "AgentRun", "Artifact"]
    assert resources[1].metadata.namespace == "demo"
    assert resources[1].metadata.ownerReferences[0].kind == ResourceKind.AGENT
    assert resources[2].spec.producedBy.name == "coder-run-1"


def test_metadata_workspace_alias_must_match_namespace() -> None:
    resources = parse_resource_documents(
        """
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: build-auth
  workspace: demo
spec:
  objective: Build authentication
"""
    )

    assert resources[0].metadata.namespace == "demo"

    with pytest.raises(ValueError, match="workspace must match"):
        parse_resource_documents(
            """
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: build-auth
  namespace: demo
  workspace: other
spec:
  objective: Build authentication
"""
        )


def test_parse_v1_registry_and_knowledge_resources() -> None:
    resources = parse_resource_documents(
        """
apiVersion: ai.platform/v1
kind: Model
metadata:
  name: stub-model
  namespace: ignored
spec:
  config:
    provider: stub
    model: stub-model
---
apiVersion: ai.platform/v1
kind: Tool
metadata:
  name: git
spec:
  description: Git operations
---
apiVersion: ai.platform/v1
kind: Capability
metadata:
  name: code-review
spec:
  requires:
    tools:
      - git
  compatibleModels:
    - stub-model
---
apiVersion: ai.platform/v1
kind: FleetTemplate
metadata:
  name: software-feature
spec:
  agents:
    - name: planner
      role: planner
      capabilities:
        - code-review
---
apiVersion: ai.platform/v1
kind: Knowledge
metadata:
  name: prd
  namespace: demo
spec:
  type: PRD
  ref: knowledge://prd.md
  relatesTo:
    - adr
"""
    )

    assert [resource.kind for resource in resources] == [
        "Model",
        "Tool",
        "Capability",
        "FleetTemplate",
        "Knowledge",
    ]
    assert resources[0].metadata.namespace is None
    assert resources[-1].metadata.namespace == "demo"


def test_parse_knowledge_index_and_context_resources() -> None:
    resources = parse_resource_documents(
        """
apiVersion: ai.platform/v1
kind: KnowledgeIndex
metadata:
  name: default
  namespace: demo
spec:
  sources:
    - knowledge://prd.md
    - ref: knowledge://architecture.md
---
apiVersion: ai.platform/v1
kind: Context
metadata:
  name: build-auth
  namespace: demo
spec:
  mission: build-auth
  query: Build authentication
  knowledgeIndex: default
"""
    )

    assert [resource.kind for resource in resources] == ["KnowledgeIndex", "Context"]
    assert resources[0].metadata.namespace == "demo"
    assert resources[0].spec.sources[0].ref == "knowledge://prd.md"
    assert resources[1].spec.knowledgeIndex == "default"


def test_template_mission_does_not_require_legacy_objective() -> None:
    resources = parse_resource_documents(
        """
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: build-auth
  namespace: demo
spec:
  template: software-feature
  inputs:
    prd:
      ref: knowledge://prd.md
  outputs:
    code: true
"""
    )

    assert resources[0].spec.template == "software-feature"
    assert resources[0].spec.objective is None


def test_mission_requires_workspace_namespace() -> None:
    with pytest.raises(ValueError, match="metadata.namespace"):
        parse_resource_documents(
            """
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: build-auth
spec:
  objective: Build authentication
"""
        )


def test_mission_requires_objective_or_template() -> None:
    with pytest.raises(ValueError, match="objective or template"):
        parse_resource_documents(
            """
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: build-auth
  namespace: demo
spec: {}
"""
        )


def test_knowledge_requires_workspace_namespace() -> None:
    with pytest.raises(ValueError, match="metadata.namespace"):
        parse_resource_documents(
            """
apiVersion: ai.platform/v1
kind: Knowledge
metadata:
  name: prd
spec:
  type: PRD
  ref: knowledge://prd.md
"""
        )


@pytest.mark.parametrize("kind", ["KnowledgeIndex", "Context"])
def test_knowledge_index_and_context_require_workspace_namespace(kind: str) -> None:
    spec = (
        """
spec:
  sources:
    - knowledge://prd.md
"""
        if kind == "KnowledgeIndex"
        else """
spec:
  mission: build-auth
  query: Build authentication
"""
    )
    with pytest.raises(ValueError, match="metadata.namespace"):
        parse_resource_documents(
            f"""
apiVersion: ai.platform/v1
kind: {kind}
metadata:
  name: default
{spec}
"""
        )


@pytest.mark.parametrize(
    "ref",
    [
        "knowledge://",
        "knowledge:///prd.md",
        "knowledge://../secret.md",
        "knowledge://briefs/./prd.md",
        "knowledge://briefs//prd.md",
        "knowledge://briefs\\prd.md",
    ],
)
def test_knowledge_refs_must_be_workspace_relative_normalized_paths(ref: str) -> None:
    with pytest.raises(ValueError, match="knowledge references"):
        parse_resource_documents(
            f"""
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: build-auth
  namespace: demo
spec:
  objective: Build authentication
  brief:
    ref: {ref}
"""
        )


def test_knowledge_index_sources_validate_refs() -> None:
    with pytest.raises(ValueError, match="knowledge references"):
        parse_resource_documents(
            """
apiVersion: ai.platform/v1
kind: KnowledgeIndex
metadata:
  name: default
  namespace: demo
spec:
  sources:
    - knowledge://../secret.md
"""
        )


def test_store_bootstraps_platform_and_rejects_status_apply(tmp_path) -> None:
    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")

    platform = store.get(ResourceKind.PLATFORM, "local")
    assert platform is not None
    assert platform["status"]["phase"] == "Ready"

    with pytest.raises(ValueError, match="status is controller-owned"):
        store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Workspace",
                "metadata": {"name": "demo"},
                "status": {"phase": "Ready"},
            }
        )


def test_store_admission_rejects_orphan_children(tmp_path) -> None:
    store = ResourceStore(f"sqlite:///{tmp_path / 'platform.db'}", tmp_path / "platform")

    with pytest.raises(ValueError, match="Workspace demo does not exist"):
        store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Mission",
                "metadata": {"name": "build-auth", "namespace": "demo"},
                "spec": {"objective": "Build authentication"},
            }
        )
