# RFC-0004: Structured Model Protocol

## Title

Structured Model Protocol.

## Authors

TBD.

## Status

Draft.

## Motivation

Once the platform supports agentic execution, YAML resources are no longer the
only stable external contract. The Decision protocol is the provider-neutral
language spoken from Model output through Pilot to the Execution Engine.

That protocol should be provider-neutral, versioned, validated, and stable
enough to outlive any one Model provider. A Pilot should be able to replace
OpenAI, Ollama, Anthropic, local models, or future providers without changing
the platform's execution semantics.

The canonical Decision protocol is now defined in the Platform Specification
[Decisions](../spec/022-decisions.md) chapter. RFC-0004 remains the design space
for provider adaptation, prompt framing, schema negotiation, and advanced
structured-output behavior.

## Background

Today the model interaction can be described as:

```text
prompt
-> markdown
```

Agentic execution requires structured decisions:

```json
{
  "version": "v1",
  "type": "invoke_tool",
  "tool": "filesystem",
  "operation": "write",
  "arguments": {
    "path": "src/Login.tsx",
    "contents": "..."
  }
}
```

or:

```json
{
  "version": "v1",
  "type": "complete",
  "artifact": {
    "kind": "ImplementationSummary",
    "summary": "Login page implemented"
  }
}
```

## Goals

- Extend or refine the provider-neutral Decision protocol when needed.
- Define Pilot-to-Model prompt framing for Decision output.
- Define provider adaptation and schema negotiation.
- Define validation, error handling, and provider-specific recovery behavior.
- Make provider replacement possible without changing platform semantics.

## Non-Goals

This RFC does not define concrete Tool Runtime behavior, loop execution, or the
full multi-turn Agent Runtime.

## Proposed Design

Model output must be parsed into the versioned Decision envelope defined by the
Platform Specification. The initial Decision types are:

- `invoke_tool`
- `complete`
- `fail`
- `request_input`

An `invoke_tool` Decision must identify Tool, operation, and structured
arguments. The Execution Engine converts the Decision into a ToolInvocation
resource only after validation.

A Decision is not a platform resource and does not bypass resource admission,
Policy, Approval, or trace contracts. It is the provider-neutral protocol object
that the Execution Engine interprets into governed platform state.

A `complete` Decision must include enough structured data to produce the
required Artifact resources or to mark the AgentRun complete according to the
effective execution strategy.

Malformed Decisions are validation failures. The Execution Engine must not infer
a ToolInvocation from unstructured prose.

Any future Platform Specification update from this RFC must remain independent
from any single provider SDK or native tool-calling feature.

## Protocol Shape

The protocol should include:

- prompt framing for the supported Decision versions
- provider capability negotiation
- provider-native structured-output translation
- optional human-readable rationale when Policy permits recording it
- validation errors and recovery hints when a provider returns invalid output

## Provider Independence

Model providers may expose native tool-calling or structured-output APIs, but
the platform protocol remains the canonical contract. Provider adapters must
translate provider-specific responses into the platform protocol before runtime
acts on them.

## Testing

The implementation should include coverage for:

- valid `invoke_tool` Decisions
- valid `complete` Decisions
- malformed JSON or schema violations
- unknown tools and operations
- provider-specific translation
- refusal or safety outputs
- protocol version mismatch

## Open Questions

- Should the protocol include explicit reasoning fields, or only externally
  observable Decisions?
- How should prompt rendering communicate the protocol schema to providers with
  different structured-output capabilities?
