# First Mission

A `Mission` declares desired state: what outcome the platform should produce.
The user writes the Mission; controllers reconcile the rest.

```text
Workspace day0
`-- Mission implement-login-page
    `-- FleetTemplate login-page
```

Apply the Mission:

```bash
platform apply day0/mission.yaml
```

Manifest:

```yaml
apiVersion: ai.platform/v1
kind: Mission
metadata:
  name: implement-login-page
  namespace: day0
spec:
  template: login-page
  inputs:
    prd:
      ref: knowledge://prd.md
    architecture:
      ref: knowledge://architecture.md
    research:
      ref: knowledge://research.md
  outputs:
    code: true
    tests: true
    review: true
```

The Mission references the `login-page` FleetTemplate and the indexed Knowledge.
It does not create Agents directly. That is the controller's job.
