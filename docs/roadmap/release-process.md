# Release Process

Platform releases communicate maturity, compatibility, and support
expectations. Release stages apply to the platform contract and implementation
readiness; they are not tied to calendar dates.

## Prototype

Prototype releases prove architecture and developer ergonomics. They may be
incomplete and may keep local-only implementation shortcuts, but they should
make architectural assumptions visible.

Expectations:

- Demonstrates core resource and reconciliation concepts.
- Prioritizes learning and specification feedback.
- May change quickly, with limited compatibility guarantees.

## Alpha

Alpha releases validate new platform contracts with early users and
contributors.

Expectations:

- Significant features have RFCs.
- Specification changes are documented before implementation.
- Resource APIs may use alpha versions for unstable contracts.
- Tests cover expected behavior and known failure modes.

## Beta

Beta releases prepare contracts for stability.

Expectations:

- Resource APIs are close to stable.
- Migration and deprecation guidance exists.
- Compatibility risks are documented.
- Operational and contributor documentation is usable.

## Stable

Stable releases commit to compatibility for the declared specification and
resource API versions.

Expectations:

- Stable resource APIs preserve field meaning and lifecycle semantics.
- Breaking changes require a new major specification or API version.
- Quality gates, tests, documentation, trace support, events, API, and CLI
  behavior are complete for the release scope.

## Release Review

Before declaring a stage transition, review:

- accepted RFCs for included features
- specification updates
- implementation completeness
- tests and quality checks
- documentation
- events and traceability
- API and CLI changes
- required ADRs
- compatibility and migration notes
