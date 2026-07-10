# Feature Lifecycle

Significant platform work follows this lifecycle:

```text
Idea
  -> RFC
  -> Architecture Review
  -> Specification Update
  -> Implementation
  -> Quality Assurance
  -> Merge
  -> ADR, if required
```

## Stages

Idea identifies the problem, desired outcome, and rough scope.

RFC turns the idea into a reviewable design when the change is significant or
architectural.

Architecture Review checks resource ownership, controller behavior, runtime
boundaries, policy, observability, API and CLI implications, compatibility, and
alignment with the Platform Specification.

Specification Update changes the normative contract before implementation when
resources, APIs, controller behavior, runtime behavior, policy, events,
traceability, or compatibility change.

Implementation realizes the accepted contract in the prototype or another
compatible implementation.

Quality Assurance verifies behavior with tests, documentation, events, trace
support, API and CLI coverage, and repository quality checks.

Merge lands the reviewed and verified change.

ADR records the durable architectural decision when the feature introduces,
changes, or supersedes one.

## Definition Of Done

A significant feature is complete only when all applicable items exist:

- accepted RFC
- updated Platform Specification
- implementation
- tests
- documentation
- trace support
- events
- API updates
- CLI updates
- ADR, if architectural

If an item does not apply, the PR or RFC must state why. For example, a
documentation-only clarification may mark implementation, API updates, CLI
updates, events, and trace support unchanged with rationale.

## No-Skip Rule

No significant feature should skip RFC, architecture review, specification
review, quality assurance, or documentation. Emergency fixes may merge with
reduced ceremony only when they do not change platform contracts; follow-up
documentation should be added promptly when they reveal a contract gap.
