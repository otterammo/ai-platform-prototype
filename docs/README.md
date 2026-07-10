# AI Platform Engineering Governance

This directory defines how the AI Platform evolves. The Platform Specification
is the contract. The prototype implementation is one realization of that
contract and must evolve toward it.

Future significant work should update governance artifacts in this order:

1. Write or update an RFC for the intended architectural change.
2. Review the design against the Platform Specification and architecture
   checklist.
3. Update the specification before implementation when the contract changes.
4. Implement, test, document, and verify the feature.
5. Record an ADR when the work creates or changes a durable architectural
   decision.

## Directory Guide

- [tutorials](tutorials/README.md) contains the Day 0 onboarding flow and
  acceptance-test-backed getting started experience.
- [spec](spec/README.md) contains the normative Platform Specification.
- [rfc](rfc/README.md) contains design proposals for significant changes.
- [adr](adr/README.md) contains permanent architecture decisions.
- [roadmap](roadmap/README.md) describes milestone-based product evolution.
- [contributing](contributing/README.md) describes contributor workflow,
  review expectations, and quality gates.

## Governance Rules

- Every feature MUST conform to the Platform Specification.
- The implementation MUST evolve toward the Platform Specification.
- The Platform Specification MUST remain implementation-agnostic.
- Breaking architectural changes MUST update the Platform Specification first.
- Significant architectural changes SHOULD begin as RFCs.
- Permanent architectural decisions SHOULD be captured as ADRs.
- No significant feature should skip architecture review, specification review,
  implementation verification, and documentation.
