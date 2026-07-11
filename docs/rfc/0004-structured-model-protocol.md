# RFC-0004: Structured Model Protocol

## Title

Structured Model Protocol.

## Authors

TBD.

## Status

Draft.

## Motivation

Once the platform supports agentic execution, YAML resources are no longer the
only stable external contract. The second major contract becomes the language
spoken between Pilot and Model.

That protocol should be provider-neutral, versioned, validated, and stable
enough to outlive any one Model provider. A Pilot should be able to replace
OpenAI, Ollama, Anthropic, local models, or future providers without changing
the platform's execution semantics.

This protocol becomes a platform API contract. If this RFC is accepted, it must
produce a dedicated Platform Specification chapter before multi-turn runtime
implementation begins.

## Background

Today the model interaction can be described as:

```text
prompt
-> markdown
```

Agentic execution requires structured decisions:

```json
{
  "type": "tool_invocation",
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
  "type": "final_response",
  "artifact": {
    "kind": "ImplementationSummary",
    "summary": "Login page implemented"
  }
}
```

## Goals

- Define the provider-neutral Pilot-to-Model decision protocol.
- Define valid decision types.
- Define validation, error handling, and versioning.
- Define how ToolInvocation requests map to RFC-0001 resources.
- Define how final responses map to Artifact production.
- Make provider replacement possible without changing platform semantics.

## Non-Goals

This RFC does not define concrete Tool Runtime behavior, loop execution, or the
full multi-turn Agent Runtime.

## Proposed Design

Model output must be parsed as a versioned decision envelope. The initial
decision types are:

- `tool_invocation`
- `final_response`

A `tool_invocation` decision must identify Tool, operation, and structured
arguments. Runtime or Pilot code converts the decision into a ToolInvocation
resource only after validation.

A `final_response` decision must include enough structured data to produce the
required Artifact resources or to mark the AgentRun complete according to Pilot
strategy.

Malformed decisions are validation failures. Runtime must not infer a
ToolInvocation from unstructured prose.

The Platform Specification update for this RFC should be independent from any
single provider SDK or native tool-calling feature.

## Protocol Shape

The protocol should include:

- Protocol version.
- Decision type.
- Correlation identifier.
- ToolInvocation request payload when applicable.
- Final response payload when applicable.
- Optional human-readable rationale when Policy permits recording it.
- Validation errors and recovery hints when a provider returns invalid output.

## Provider Independence

Model providers may expose native tool-calling or structured-output APIs, but
the platform protocol remains the canonical contract. Provider adapters must
translate provider-specific responses into the platform protocol before runtime
acts on them.

## Testing

The implementation should include coverage for:

- valid tool invocation decisions
- valid final responses
- malformed JSON or schema violations
- unknown tools and operations
- provider-specific translation
- refusal or safety outputs
- protocol version mismatch

## Open Questions

- Should the protocol include explicit reasoning fields, or only externally
  observable decisions?
- Should final responses create Artifacts directly or return an Artifact intent
  for runtime validation?
- How should prompt rendering communicate the protocol schema to providers with
  different structured-output capabilities?
