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
