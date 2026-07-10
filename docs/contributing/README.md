# Contributing

Contributors evolve the Platform Specification first and implementation second.
The specification is the contract; the prototype is one implementation of that
contract.

## Repository Structure

- `ai_platform/` contains the prototype implementation.
- `tests/` contains implementation tests.
- `examples/` contains demo resources and knowledge.
- `docs/spec/` contains the normative Platform Specification.
- `docs/rfc/` contains proposals for significant architectural changes.
- `docs/adr/` contains permanent architecture decisions.
- `docs/roadmap/` contains milestone-based planning.
- `docs/contributing/` contains contributor workflow and review guidance.

## Documentation Workflow

- Update the Platform Specification before implementation when a public
  platform contract changes.
- Start significant architectural work with an RFC.
- Add an ADR when a durable architectural decision is made or changed.
- Keep implementation-specific tutorials and examples outside normative spec
  text unless they clarify the contract.

## Coding Standards

- Keep changes scoped to the feature or fix under review.
- Prefer existing resource, controller, runtime, policy, and storage patterns.
- Keep model provider behavior isolated from resource and controller contracts.
- Preserve level-based reconciliation, explicit ownership, policy enforcement,
  events, and traceability.

## Testing Expectations

Run the Makefile quality targets from the repository root:

```bash
make fmt
make lint
make typecheck
make test
make check
```

Run `pre-commit run --all-files` when changing YAML, TOML, Markdown, hooks, CI,
`.gitignore`, or repository hygiene files.

## Architecture Review

Every significant PR should answer the
[architecture review checklist](architecture-review-checklist.md). Reviewers
should reject changes that bypass the specification, weaken ownership, make
reconciliation non-idempotent, hide runtime side effects, skip policy, or reduce
traceability.

## RFC Workflow

Use [RFC template](../rfc/template.md) for significant changes. Keep RFCs in
`Draft` until the design is ready for review, then move to `Proposed`. Accepted
RFCs that change the contract must update `docs/spec/` before implementation.

## ADR Workflow

Use [ADR template](../adr/template.md) when a durable architectural decision is
made. ADRs should describe context, decision, consequences, and alternatives.
When a decision changes, add a new ADR instead of rewriting history.
