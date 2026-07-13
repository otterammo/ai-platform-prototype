---
name: platform-architecture-review
description: Review AI Platform proposals or diffs for architecture drift, primitive expansion, ownership, reconciliation, runtime boundaries, policy, traceability, spec alignment, and compatibility.
---

# Platform Architecture Review

Use this skill before significant implementation or during PR review.

## Review Mindset

Default answer: use the existing architecture. Recommend a new primitive only
when existing resources, status, events, policy, runtime frames,
ToolInvocation, Decision, Model, Knowledge, or provider adapters are
demonstrably insufficient.

## Checklist

- Uses existing primitives unless insufficiency is demonstrated.
- RFC exists for significant architectural change.
- ADR exists or is planned for durable decisions.
- Platform Specification remains the source of truth.
- Resource API compatibility is preserved or versioned.
- Controller ownership is clear.
- Runtime does not absorb control-plane responsibilities.
- Policy, approval, events, and traceability are not bypassed.
- Naming matches the glossary and existing resource language.
- Non-goals prevent scope expansion.

## Output

Lead with findings ordered by severity. Include:

- Blocking architecture issues.
- Missing non-goals or compatibility analysis.
- Specification sections that must change.
- ADR requirements.
- Dogfood or QA evidence needed before acceptance.
