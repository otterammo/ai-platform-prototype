# Platform Specification v1.3.0

## Purpose

This specification defines the v1.3.0 architecture and behavioral contracts for
the AI Platform. It is the normative source of truth for resources,
controllers, runtimes, APIs, command-line tools, and extensions that claim
compatibility with `ai.platform/v1`.

The platform is a declarative control plane for AI work. Users declare desired
outcomes as resources. The control plane admits, persists, reconciles, schedules,
and observes those resources. Runtimes execute scheduled work and report results
back through resource status and events.

## Audience

This specification is written for platform engineers, controller authors,
runtime authors, SDK developers, plugin authors, and future contributors. A
reader MUST be able to understand the platform architecture without reading any
particular codebase.

## Normative Language

The key words `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` are to be
interpreted as described in RFC 2119 and RFC 8174 when, and only when, they
appear in uppercase.

Non-normative examples are illustrative only. If an example conflicts with a
normative statement, the normative statement takes precedence.

## Compatibility

The v1 API group is `ai.platform/v1`. A compatible control plane MUST preserve
the semantics defined in this specification for every v1 resource kind it
supports. A compatible runtime MUST honor runtime boundaries, policy decisions,
context consumption rules, and artifact reporting rules defined here.

The specification is intentionally architecture-first. Existing systems SHOULD
evolve toward this specification. This specification MUST NOT be reduced to match
the incidental limits of any single system.

The specification index is maintained in [README.md](README.md). Versioning
rules for the Platform Specification and resource APIs are defined in
[Versioning](020-versioning.md).

## Foundational Protocols

The platform has three foundational protocols:

- Declarative Resources define desired and observed platform state.
- Events define immutable lifecycle and audit history.
- Decisions define provider-neutral Model intent interpreted by the Execution
  Engine.

## Platform Goals

The platform has the following goals:

- Provide a declarative API for AI work.
- Separate desired state from execution.
- Make orchestration observable and replayable through resources and events.
- Keep model providers replaceable.
- Keep tools and side effects governable by policy.
- Make knowledge retrieval explicit through Context.
- Support extension without weakening platform contracts.

## Non-Goals

The specification does not mandate a database, programming language, process
model, transport library, model vendor, vector store, queue, or file layout. It
does not define tutorials, operator runbooks, or contributor workflow.
