# RFC Index

RFCs describe significant architectural changes before implementation. They are
the review surface for proposals that change resources, APIs, controllers,
runtime behavior, policy, traceability, or compatibility.

Use [template.md](template.md) for new RFCs.

## Statuses

- `Draft`: The idea is being shaped.
- `Proposed`: The RFC is ready for architecture review.
- `Accepted`: The design is approved and may update the specification.
- `Implementing`: Implementation work is in progress.
- `Implemented`: The accepted design has landed and passed quality review.
- `Rejected`: The proposal will not proceed.
- `Superseded`: A newer RFC or specification change replaces this proposal.

## Process

1. Create an RFC from the template.
2. Keep it in `Draft` until motivation, goals, non-goals, and design are clear.
3. Move it to `Proposed` for architecture review.
4. If accepted, update the Platform Specification before implementation when
   the public contract changes.
5. Move it through `Implementing` and `Implemented` as the work lands.

Significant features should not bypass this process.
