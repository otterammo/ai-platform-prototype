---
name: platform-bug-lifecycle
description: Handle AI Platform bug fixes through reproduce, investigate, fix, regression test, dogfood, and release while preserving contracts and adding evidence.
---

# Platform Bug Lifecycle

Use this skill for defects that need evidence, a focused fix, regression
coverage, and release or documentation follow-up.

## Workflow

```text
Reproduce -> Investigate -> Fix -> Regression Test -> Dogfood -> Release
```

## Checklist

- Reproduction is documented.
- Expected behavior is grounded in the Platform Specification.
- Root cause or probable defect is identified before the fix.
- Fix is scoped to the responsible surface.
- Regression test fails before the fix when practical.
- Public contract changes are avoided unless explicitly governed.
- Dogfood or tutorial impact is reviewed.
- Release note or roadmap impact is recorded when user-visible.

## Output

- Reproduction steps.
- Fix summary.
- Regression coverage.
- Verification commands.
- Residual risk.
