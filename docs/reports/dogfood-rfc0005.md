# RFC-0005 Autonomous Dogfood

## Primary Workflow

RFC-0005 replaces the manual ToolInvocation walkthrough with one autonomous
AgentRun:

```text
Apply Mission
-> approve guarded actions
-> watch execution
-> inspect Artifact and trace
```

The user should not create ToolInvocation YAML for the normal path. The
Execution Engine owns ToolInvocation creation after the model returns canonical
`invoke_tool` Decisions.

## Initial Workload

The first supported autonomous repository workload is:

1. Inspect the repository.
2. Read `README.md`.
3. Write `SUMMARY.md`.
4. Stage `SUMMARY.md`.
5. Commit `SUMMARY.md`.
6. Complete.

The expected execution shape is:

```text
Decision invoke_tool filesystem.read
-> ToolInvocation
-> Observation
-> Decision invoke_tool filesystem.write
-> ToolInvocation
-> Observation
-> Decision invoke_tool git.status
-> ToolInvocation
-> Observation
-> Decision invoke_tool git.add
-> ToolInvocation
-> Observation
-> Decision invoke_tool git.commit
-> ToolInvocation
-> Observation
-> Decision complete
-> Artifact
```

## What This Validates

- Provider output is normalized into canonical Decisions before Runtime acts.
- The Execution Engine never inspects provider-specific JSON.
- ToolInvocation names are deterministic, so resume does not duplicate side
  effects.
- Filesystem, Git, and Shell operations execute through the same governed
  runtime boundary.
- Tool failures become Observations for the next model turn unless a runtime
  budget is exhausted.
- Policy and Approval gates pause execution without reinvoking the model.
- ExecutionFrames and trace reconstruct Decision, ToolInvocation, Observation,
  retry, approval, and completion history.

## Manual ToolInvocations

Direct ToolInvocation manifests remain useful for debugging one runtime
operation or reproducing a tool contract issue. They are no longer the primary
dogfood flow.
