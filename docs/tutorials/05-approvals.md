# Approvals

Policy controls runtime side effects. In this tutorial, the default Policy
requires approval before the implementer Agent can use the `git` tool.

```text
AgentRun
`-- Policy
    `-- Approval Pending
        `-- Approval Approved
            `-- AgentRun resumes
```

List pending approvals:

```bash
platform approvals
```

Choose the approval name from the output, then inspect it:

```bash
platform describe approval <approval-name>
```

Approve the action:

```bash
platform approve <approval-name> --by day0-user --reason "Day 0 tutorial"
```

To stop a waiting run instead of approving it, request cancellation on the
AgentRun:

```bash
platform cancel agentrun <run-name> -n day0
```

Wait for completion:

```bash
platform wait mission implement-login-page -n day0 --for phase=Completed --reconcile --timeout 30
```

Confirm the approval is visible in trace:

```bash
platform trace mission implement-login-page -n day0
```

The trace should include the approval request, approval grant, AgentRun resume,
ExecutionFrames, and Mission completion.
