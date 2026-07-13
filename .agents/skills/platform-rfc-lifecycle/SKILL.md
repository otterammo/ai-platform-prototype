---
name: platform-rfc-lifecycle
description: Manage AI Platform RFC work from draft through review, revise, accept, implement, QA, dogfood, merge, and implemented status. Use when drafting, reviewing, publishing, accepting, or updating RFCs.
---

# Platform RFC Lifecycle

Use this skill for significant platform changes that may affect resources, APIs,
controllers, runtime behavior, policy, traceability, compatibility, or platform
architecture.

## Source Of Truth

- RFC process: `docs/rfc/README.md`
- RFC template: `docs/rfc/template.md`
- Platform Specification: `docs/spec/README.md`
- ADR process: `docs/adr/README.md`
- Architecture checklist: `docs/contributing/architecture-review-checklist.md`

## Workflow

```text
Draft -> Review -> Revise -> Accept -> Implement -> QA -> Dogfood -> Merge -> Implemented
```

## Draft Or Publish

- Use `docs/rfc/template.md`.
- Keep scope to one architectural boundary.
- State motivation, goals, non-goals, design, compatibility, and alternatives.
- Link relevant spec chapters, ADRs, roadmap items, tutorials, and dogfood
  reports.
- Mark status according to `docs/rfc/README.md`.

## Review

- Decide whether existing platform abstractions are sufficient.
- Identify blocking architecture issues.
- Check resource ownership, controller behavior, runtime boundaries, policy,
  events, traceability, API, CLI, and compatibility.
- Name spec sections that must change.
- Name required ADRs, QA evidence, and dogfood evidence.

## Accept

- Move the RFC to `Accepted` only after review concerns are resolved.
- Update the Platform Specification before implementation when public contracts
  change.
- Identify implementation, test, dogfood, tutorial, release, and ADR follow-up.

## Implemented

- Move to `Implemented` only after implementation, tests, docs, QA, dogfood, and
  release readiness are complete or explicitly marked not applicable.
