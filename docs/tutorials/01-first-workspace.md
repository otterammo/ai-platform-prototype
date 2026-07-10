# First Workspace

A `Workspace` is the isolation boundary for project resources. Namespaced
resources such as Missions, Knowledge, Agents, AgentRuns, Context, and Artifacts
belong to a Workspace.

```text
Platform
`-- Workspace day0
```

Apply the Workspace manifest:

```bash
platform apply day0/workspace.yaml
```

Manifest:

```yaml
apiVersion: ai.platform/v1
kind: Workspace
metadata:
  name: day0
spec:
  rootPath: ../day0
```

Verify it exists:

```bash
platform get workspaces
```

The `rootPath` points at the `day0/` directory you copied in the repository
root. With `AI_PLATFORM_ROOT=.platform`, the relative value `../day0` resolves
to that directory.
