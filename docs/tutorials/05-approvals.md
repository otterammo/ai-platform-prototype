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

Wait for completion:

```bash
platform wait mission implement-login-page -n day0 --for phase=Completed --reconcile --timeout 30
```

Confirm the approval is visible in trace:

```bash
platform trace mission implement-login-page -n day0
```

The trace should include the approval request, approval grant, AgentRun resume,
and Mission completion.
