# RFC Index

RFCs describe significant architectural changes before implementation. They are
the review surface for proposals that change resources, APIs, controllers,
runtime behavior, policy, traceability, or compatibility.

Use [template.md](template.md) for new RFCs.

## RFCs

- [RFC-0001: Tool Invocation Framework](0001-tool-invocation-framework.md)
  - Implemented in Platform Specification `v1.1.0` by
    [PR #12](https://github.com/otterammo/ai-platform-prototype/pull/12).
- [RFC-0002: AgentRun Execution Engine Loop](0002-agentrun-execution-engine-loop.md)
  - Implemented in Platform Specification `v1.3.0` by
    [PR #15](https://github.com/otterammo/ai-platform-prototype/pull/15).
- [RFC-0003: Built-In Tool Runtime](0003-built-in-tool-runtime.md)
  - Implementing.
- [RFC-0004: Structured Model Protocol](0004-structured-model-protocol.md)
  - Accepted in Platform Specification `v1.4.0`.
- [RFC-0005: Multi-Turn Agent Runtime](0005-multi-turn-agent-runtime.md)
  - Draft.

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
