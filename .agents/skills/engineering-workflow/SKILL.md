---
name: engineering-workflow
description: Route AI Platform engineering work through the repo's RFC, spec, implementation, review, QA, dogfood, release, and incident workflows. Use when planning, implementing, reviewing, testing, documenting, releasing, or investigating platform changes.
---

# Engineering Workflow

Use this skill to route work to the focused repo skills. Do not expect Codex to
read arbitrary checklist, template, workflow, or prompt folders automatically;
use skills for reusable behavior.

## Source Of Truth

- The normative platform contract is `docs/spec/README.md`.
- Significant architectural changes follow `docs/rfc/README.md`.
- Durable decisions follow `docs/adr/README.md`.
- Contributor lifecycle and architecture review live in `docs/contributing/`.
- Release readiness follows `docs/roadmap/release-process.md`.

## Routing

Use these focused skills when their descriptions match the task:

- `platform-rfc-lifecycle`: draft, review, publish, accept, or implement RFCs.
- `platform-implement-rfc`: implement an accepted RFC or spec-backed feature.
- `platform-architecture-review`: review architecture, primitives, ownership,
  policy, and compatibility.
- `platform-implementation-review`: review implementation diffs.
- `platform-spec-sync`: synchronize implementation, RFCs, ADRs, and spec text.
- `platform-dogfood`: run real workloads and write dogfood reports.
- `platform-release`: prepare release notes, roadmap updates, and merge
  readiness.
- `platform-incident-review`: reconstruct incidents and plan regression
  coverage.
- `platform-bug-lifecycle`: reproduce, investigate, fix, regression test,
  dogfood, and release a bug fix.

## Operating Rule

Prefer the existing architecture. New primitives, public resource changes,
runtime contract changes, policy changes, and compatibility changes should
start with RFC/spec review before implementation.

## Verification

For repository changes, use `.agents/skills/repo-quality/SKILL.md`.
