---
name: platform-implement-rfc
description: Implement an accepted AI Platform RFC or spec-backed feature while preserving resource ownership, reconciliation, policy, runtime boundaries, tests, and docs. Use for accepted RFC implementation or significant feature work.
---

# Platform Implement RFC

Use this skill when an accepted RFC or specification-backed feature is ready for
implementation.

## Before Editing

- Read the accepted RFC.
- Read linked Platform Specification chapters.
- Read relevant ADRs.
- Identify the owning implementation files.
- Identify the tests that should prove behavior.
- Identify documentation, tutorial, glossary, roadmap, and dogfood impact.

## Implementation Checklist

- Scope is tied to a request, issue, RFC, or spec change.
- Existing architecture can express the behavior.
- Public contract changes updated `docs/spec/` first.
- Resource ownership and status ownership are explicit.
- Reconciliation remains level-based and idempotent.
- Policy checks occur before side effects.
- Events and trace output explain material decisions and failures.
- API and CLI behavior are covered when affected.
- Tests cover success, failure, and compatibility risk.
- Docs, tutorials, glossary, roadmap, and dogfood impact are reviewed.

## Runtime Checklist

- AgentRun remains the executable boundary.
- Runtime consumes prepared context instead of querying Knowledge directly.
- ToolInvocation and Observation contracts are respected.
- Decision protocol behavior is unchanged unless specified.
- Budgets, retry, cancellation, resume, and terminal states are deterministic.
- ExecutionFrames are complete enough for replay and incident review.
- Side effects are explicit, policy-aware, and traceable.
- Artifacts are represented durably when required.

## Provider Checklist

- Provider behavior stays isolated in adapter boundaries.
- Resource and controller contracts do not depend on provider internals.
- Structured model protocol expectations are explicit.
- Provider failures surface as status, events, or observations as appropriate.
- Conformance tests cover request, response, error, and retry behavior.
- Secrets and provider credentials are not exposed in traces or artifacts.

## Verification

- Run the relevant targeted tests.
- Run `make check`.
- Run `pre-commit run --all-files` for Markdown, TOML, YAML, hooks, CI, or
  repository hygiene changes.
