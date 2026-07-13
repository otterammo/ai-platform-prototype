---
name: platform-implementation-review
description: Review AI Platform implementation diffs for correctness, missing tests, architecture drift, naming, ownership, policy bypass, replay safety, compatibility, and documentation drift.
---

# Platform Implementation Review

Use this skill for code or documentation implementation review.

## Checklist

- Behavior matches the governing RFC, spec, or request.
- Tests cover success, failure, and compatibility risk.
- Architecture does not drift or add unnecessary primitives.
- Resource ownership and status ownership are explicit.
- Reconciliation remains idempotent.
- Runtime boundaries are preserved.
- Policy checks occur before side effects.
- Replay safety and deterministic naming are preserved.
- Events and trace output explain material decisions and failures.
- API, CLI, docs, tutorials, glossary, roadmap, release, and dogfood impact are
  handled or marked not applicable.

## Output

Lead with findings. Include file and line references where available. If no
issues are found, say so and identify remaining test or dogfood gaps.
