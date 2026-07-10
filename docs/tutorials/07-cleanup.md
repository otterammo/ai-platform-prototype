# Cleanup

Deleting a parent resource removes controller-owned child resources from the
store. Artifact files remain on disk so generated output is not silently lost.

Delete the Mission:

```bash
platform delete mission implement-login-page -n day0
```

Verify the owned workload resources are gone:

```bash
platform get missions -n day0
platform get fleets -n day0
platform get agents -n day0
platform get agentruns -n day0
platform get artifacts -n day0
```

Delete the approval and Workspace:

```bash
platform approvals
platform delete approval <approval-name>
platform delete workspace day0
```

Verify the Workspace boundary is empty:

```bash
platform get workspaces
platform events --kind Workspace --name day0 --limit 10
```

You have now completed the Day 0 flow:

```text
install -> workspace -> knowledge -> mission -> approval -> artifact -> cleanup
```
