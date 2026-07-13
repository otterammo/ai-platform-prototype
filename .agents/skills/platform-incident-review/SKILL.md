---
name: platform-incident-review
description: Investigate AI Platform failures from trace, timeline, events, status, logs, and ExecutionFrames; produce root cause, probable defect, remediation, and regression plan.
---

# Platform Incident Review

Use this skill when investigating a failed run, regression, or operational
incident.

## Workflow

```text
Collect Trace -> Reconstruct Timeline -> Root Cause -> Fix -> Regression
```

## Inputs

- Trace.
- Timeline.
- Events.
- Status history.
- Logs.
- ExecutionFrames.
- Reproduction steps.

## Rules

- Reconstruct the timeline before naming a cause.
- Separate confirmed facts from hypotheses.
- Never speculate beyond available evidence.
- Prefer the smallest remediation that preserves existing contracts.

## Output

- Timeline reconstruction.
- Root cause analysis with evidence.
- Probable defect.
- User impact.
- Remediation options.
- Regression tests.
- Documentation or dogfood follow-up.
