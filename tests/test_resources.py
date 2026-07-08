from __future__ import annotations

import pytest

from ai_platform.resources import parse_resource_documents


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
