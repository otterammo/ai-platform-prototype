# Artifacts

An `Artifact` is durable output produced by an AgentRun. The local worker writes
markdown files under the Workspace root and records Artifact resources in the
control plane.

```text
AgentRun
`-- Artifact
    `-- day0/artifacts/implement-login-page/*.md
```

List artifacts:

```bash
platform get artifacts -n day0
```

Describe the generated Artifact:

```bash
platform describe artifact implement-login-page-fleet-implementer-run-1-artifact -n day0
```

The Artifact status includes the absolute file path. Read the file:

```bash
sed -n '1,120p' day0/artifacts/implement-login-page/implement-login-page-fleet-implementer-run-1.md
```

The stub model output is deterministic. It should include the interpreted
Mission, retrieved Knowledge context, and requested outputs.
