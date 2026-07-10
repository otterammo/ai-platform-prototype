# First Knowledge

Knowledge is project context the platform can index and retrieve for AgentRuns.
This tutorial uses three markdown files:

- `day0/knowledge/prd.md`
- `day0/knowledge/architecture.md`
- `day0/knowledge/research.md`

```text
Workspace day0
|-- Knowledge login-prd
|-- Knowledge login-architecture
|-- Knowledge login-research
`-- KnowledgeIndex default
```

Apply the registry, policy, Knowledge, and KnowledgeIndex manifest:

```bash
platform apply day0/knowledge.yaml
```

The manifest also installs the stub `Model`, builtin `Tool` resources, the
`implement-login-page` `Capability`, a one-agent `FleetTemplate`, and the
default `Policy` used later for approval.

Build the KnowledgeIndex:

```bash
platform knowledge index -n day0
```

Search it:

```bash
platform knowledge search login -n day0
```

You should see chunks from `prd.md`, `architecture.md`, or `research.md`.

The complete manifest is checked in at `day0/knowledge.yaml`; the important
Knowledge section is:

```yaml
apiVersion: ai.platform/v1
kind: Knowledge
metadata:
  name: login-prd
  namespace: day0
spec:
  type: PRD
  ref: knowledge://prd.md
---
apiVersion: ai.platform/v1
kind: KnowledgeIndex
metadata:
  name: default
  namespace: day0
spec:
  sources:
    - knowledge://prd.md
    - knowledge://architecture.md
    - knowledge://research.md
```

`knowledge://` references are resolved under the Workspace `knowledge/`
directory, so Agents consume indexed context without reading arbitrary files.
