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

## Live Ollama Validation

On 2026-07-13, the RFC-0005 path was validated with live Ollama model
`gpt-oss:20b` and an OpenAI-compatible endpoint at
`http://100.114.23.127:11434/v1`.

Validation workspace:

```text
/var/folders/r5/1m72lm1d65x12xwpf2vprb0m0000gn/T/rfc0005-live-5caxz33_/workspace
```

Only declarative resources were seeded: Workspace, Model, Tool, Capability,
FleetTemplate, Policy, and Mission. No manual ToolInvocation resources were
created.

Observed runtime-created operations:

1. `filesystem.read` with `{"path": "README.md"}`
2. `filesystem.write` with `{"path": "SUMMARY.md", ...}`
3. `git.status` with `{}`
4. `git.add` with `{"path": "SUMMARY.md"}`
5. `git.commit` with `{"message": "docs: add live dogfood summary"}`

Results:

- Mission phase: `Completed`.
- AgentRun phase: `Succeeded`.
- Decision rejection count: `0`.
- All Decision frames were canonical.
- Observations were delivered after each tool frame and fed the next prompt.
- `git.commit` required approval; repeated reconciliation while waiting kept
  model invocations at `0`, ToolInvocation count at `5`, and the ToolInvocation
  snapshot stable.
- Trace included ExecutionFrame, Decision, ToolInvocation, Observation, and
  Approval events.
- Exactly one Git commit was created:
  `431862afc5d18725e4c639a44ae3c99d0ac265f4`.

## Manual ToolInvocations

Direct ToolInvocation manifests remain useful for debugging one runtime
operation or reproducing a tool contract issue. They are no longer the primary
dogfood flow.
