---
name: platform-spec-sync
description: Synchronize AI Platform implementation, tests, RFCs, ADRs, tutorials, dogfood reports, and Platform Specification text. Use when behavior and spec may be out of sync.
---

# Platform Spec Sync

Use this skill when behavior and specification may be out of sync.

## Compare

- Implementation behavior.
- Tests and fixtures.
- RFCs and ADRs.
- Tutorials and examples.
- Dogfood reports.
- Platform Specification chapters.

## Checklist

- Text is implementation-agnostic.
- Normative requirements are precise and testable.
- Examples do not override normative statements.
- Resource fields, status, events, and lifecycle semantics are complete.
- API, CLI, policy, trace, and compatibility implications are covered.
- Glossary terms match `docs/spec/018-glossary.md`.
- Versioning policy is followed for compatibility changes.
- Linked RFCs and ADRs explain why the contract changed.

## Output

Identify where the spec is missing, outdated, too implementation-specific, or
contradicted by behavior. Prefer updating the specification before
implementation when public contracts change.
