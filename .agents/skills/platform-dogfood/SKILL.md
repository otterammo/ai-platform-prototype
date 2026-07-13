---
name: platform-dogfood
description: Run real AI Platform workloads through documented CLI, API, tutorial, or example paths and produce dogfood findings with UX, docs, implementation, test, and architecture categories.
---

# Platform Dogfood

Use this skill when a feature needs real workflow validation.

## Checklist

- Workload uses documented CLI, API, tutorial, or example paths.
- Setup and cleanup steps are reproducible.
- Expected behavior is stated before execution.
- Actual behavior includes commands, outputs, artifacts, events, and trace data.
- Findings are classified as UX, docs, implementation, test, or architecture.
- Architectural findings include evidence that existing abstractions are
  insufficient.
- Follow-up recommendations name an owner and verification path.

## Report Template

```md
# Dogfood Report

## Workload

## Expected Behavior

## Actual Behavior

## Evidence

- Commands:
- Artifacts:
- Events:
- Trace:

## Findings

- UX:
- Documentation:
- Implementation:
- Test:
- Architecture:

## Follow-Up
```

## Rule

Do not call friction architectural unless existing abstractions are shown to be
insufficient.
