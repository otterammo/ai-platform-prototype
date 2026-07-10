# Observing The Platform

Reconciliation turns desired state into owned child resources.

```text
Mission
`-- Controller
    `-- Fleet
        `-- Controller
            `-- Agent
                `-- Controller
                    `-- AgentRun
                        `-- Scheduler
                            `-- Worker
                                `-- Artifact
```

Run reconciliation until the Mission reaches the approval gate:

```bash
platform wait mission implement-login-page -n day0 --for phase=Waiting --reconcile --timeout 30
```

Inspect the hierarchy:

```bash
platform get missions -n day0
platform get fleets -n day0
platform get agents -n day0
platform get agentruns -n day0
```

The Mission should be `Waiting`. The AgentRun is paused because policy requires
approval for the implementer Agent's `git/use` action.

Inspect events and trace:

```bash
platform events -n day0 --limit 20
platform timeline mission implement-login-page -n day0
platform trace mission implement-login-page -n day0
```

Use `describe` when you want desired state, observed state, child resources,
recent events, Knowledge usage, and artifacts in one view:

```bash
platform describe mission implement-login-page -n day0
```

Concepts introduced here:

- Controllers create or update child resources.
- The scheduler moves ready AgentRuns to `Scheduled`.
- The local worker executes scheduled AgentRuns.
- Runtime consumes `Context`, evaluates `Policy`, invokes the model, and writes
  Artifacts only after policy allows the action.
