# RFC-0004: Structured Model Protocol

## Title

Structured Model Protocol.

## Authors

TBD.

## Status

Draft.

## Formal Review Date

2026-07-12.

## Formal Review Conclusion

Revise.

RFC-0004 should not be implemented until it defines the Pilot-to-Model protocol
as a durable provider-neutral platform contract. The current draft has the
correct architectural direction, but it is not yet precise enough for
provider-adapter implementation because it does not fully specify version
negotiation, provider normalization, streaming, error ownership, metadata, and
conformance testing.

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
  "summary": "Login page implemented",
  "outputs": [
    {
      "type": "workspace-change",
      "ref": "workspace://..."
    }
  ]
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

A `complete` Decision must include `summary` and `outputs` fields. The
Execution Engine validates required Mission outputs and creates a final summary
Artifact unless the Mission explicitly disables it.

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

The protocol can support OpenAI, Ollama, Anthropic, Gemini, local models, and
future providers without changing the Execution Engine only if the Execution
Engine receives the same canonical Decision envelope from every Pilot adapter.
Provider-native request and response shapes MUST remain behind Pilot/provider
adapter boundaries.

Provider adapters MAY use JSON mode, function or tool calling, structured output
APIs, grammar-constrained decoding, or plain text parsing. All of these paths
MUST normalize into the same platform Decision schema before execution.

Plain text parsing is a fallback, not a peer contract. It MAY be used for
providers without structured-output support, but it must still produce a valid
Decision or a structured parse failure. The Execution Engine MUST NOT infer a
Decision directly from provider prose.

## Canonical Representation

The platform should define one canonical Decision schema per supported Decision
protocol version. Provider-specific schemas may exist only inside adapters as
translation contracts.

The Execution Engine should consume `Decision v1`, not OpenAI tool calls,
Anthropic tool-use blocks, Gemini function calls, Ollama JSON blobs, or local
grammar decoder internals. This preserves one platform contract while allowing
provider adapters to choose the strongest structured-response mechanism each
provider supports.

## Pilot Responsibilities

Pilot owns:

- Prompt construction.
- Provider selection and provider adaptation.
- Provider-specific request framing.
- Response parsing.
- Validation that provider output can be represented as a Decision.
- Decision production or structured parse/adapter failure.

Pilot does not own:

- AgentRun execution.
- Infrastructure retry decisions.
- ToolInvocation creation.
- Policy or Approval decisions.
- Orchestration across Missions, Fleets, Agents, or AgentRuns.
- Terminal AgentRun state selection.

Execution Engine remains the only component that validates a produced Decision
against platform execution context and converts it into platform action.

## Structured Responses

Structured Decision production should be specified as an adapter normalization
pipeline:

```text
ExecutionFrame
-> Pilot prompt and provider request
-> Provider-native response
-> Provider adapter parsing
-> Canonical Decision envelope
-> Execution Engine
```

Provider mechanisms map as follows:

| Provider mechanism | Pilot adapter responsibility |
| --- | --- |
| JSON mode | Provide the Decision JSON schema in prompt/request framing; parse one JSON object; reject extra prose or multiple objects unless explicitly supported. |
| Function/tool calling | Map provider tool-call name and arguments to a canonical Decision type and payload; never treat the provider tool call as a ToolInvocation. |
| Structured output APIs | Bind the provider response schema to the canonical Decision schema for the negotiated Decision version. |
| Grammar-constrained decoding | Use a grammar generated from the canonical Decision schema and parse the result into the same envelope. |
| Plain text parsing | Extract a Decision only when parsing is deterministic and schema-valid; otherwise return a parse failure. |

## Decision Versioning

The Execution Engine should declare supported Decision protocol versions, and
Pilot/provider adapters should negotiate one supported version before the model
request.

`Decision v1` is the only version accepted by the current specification.
Future versions should be introduced by a Platform Specification update and a
compatibility statement:

- Backward-compatible extensions add optional fields or new Decision types that
  older engines reject deterministically.
- Incompatible versions use a new Decision version value, for example `v2`.
- A Pilot MUST NOT send an unsupported Decision version to the Execution Engine
  after negotiation.
- If an unsupported version is received, the Execution Engine rejects it with
  `DecisionVersionUnsupported` and performs no side effect.

## Validation Boundary

Validation should avoid duplicate ownership:

- Pilot validates provider transport success, provider response completeness,
  provider-native structured output shape, parseability, and whether the parsed
  object can be represented as a canonical Decision envelope.
- Execution Engine validates Decision protocol version, Decision type, required
  fields, semantic consistency with the current AgentRun, Agent capabilities,
  Tool contract arguments, execution budgets, and policy admission where
  applicable.

Pilot may use the canonical schema to avoid sending obviously malformed
Decisions, but Execution Engine remains the authoritative validator before any
platform action occurs.

## Streaming

Decision production should not stream partial Decisions to the Execution Engine
in RFC-0004.

Provider token streams MAY be consumed inside the Pilot adapter for latency,
debugging, cancellation, timeout handling, or progressive parsing. A Decision is
complete only when the adapter has received the provider's terminal response,
parsed exactly one canonical Decision envelope, and either accepted it as
well-formed or returned a structured failure.

Trace may record provider streaming metadata such as first-token latency, token
counts, finish reason, truncation, or adapter parse progress when policy permits
it. Trace MUST NOT record partial provider text as a Decision and MUST NOT allow
the Execution Engine to act on partial output.

## Error Model

Provider and adapter failures should map to stable failure categories before
Execution Engine retry policy is applied:

| Failure | Owner | Engine input |
| --- | --- | --- |
| Provider timeout | Pilot/provider adapter detects transport timeout | Structured model invocation failure with retryability metadata. |
| Provider rate limit or availability error | Pilot/provider adapter | Structured provider failure with provider metadata and retry hint. |
| Malformed JSON or unparsable output | Pilot/provider adapter | `DecisionParseFailed` candidate failure. |
| Provider-native schema mismatch | Pilot/provider adapter | Structured adapter validation failure. |
| Canonical Decision schema invalid | Execution Engine | `DecisionValidationFailed`. |
| Unsupported Decision version | Execution Engine | `DecisionVersionUnsupported`. |
| Unsupported Decision type | Execution Engine | `DecisionTypeUnsupported`. |
| Tool or operation unavailable | Execution Engine | `CapabilityViolation`. |
| Invalid Tool arguments | Execution Engine | `ToolArgumentsInvalid`. |
| Model refusal | Pilot/provider adapter | Structured refusal outcome; Engine retry/fail behavior follows AgentRun policy. |
| Truncated response or finish reason length | Pilot/provider adapter | Structured provider failure or parse failure with truncation metadata. |

The RFC should specify which failures consume `maxDecisionFailures`, which
consume model invocation retry budgets, and which are terminal.

## Provider Metadata

Provider metadata belongs beside, not inside, Decision semantics. The canonical
Decision should remain the provider-neutral instruction. Execution metadata
should be recorded in AgentRun status, ExecutionFrame data, Events, or trace as
policy allows.

Recommended metadata:

- Platform Model resource identity.
- Provider name.
- Provider model identifier.
- Provider request ID when available.
- Latency and timeout metadata.
- Input, output, total, and reasoning token usage when reported.
- Finish reason.
- Refusal or safety category when reported.
- Structured-output mechanism used.
- Adapter name and version.

The platform MUST represent unavailable token usage as unknown rather than
fabricating counts.

## Testing

The implementation should include coverage for:

- valid `invoke_tool` Decisions
- valid `complete` Decisions
- malformed JSON or schema violations
- unknown tools and operations
- provider-specific translation
- refusal or safety outputs
- protocol version mismatch

Before implementation, RFC-0004 should require:

- Protocol conformance tests that every provider adapter must pass.
- Golden canonical Decision fixtures for each Decision type.
- Provider-native input/output fixtures for OpenAI, Ollama, Anthropic, Gemini,
  and at least one local model path or grammar-constrained adapter.
- Compatibility matrix covering structured-output mechanism, streaming support,
  token usage reporting, refusal reporting, finish reasons, and unsupported
  capabilities.
- Negative fixtures for malformed JSON, invalid canonical schema, unsupported
  version, unknown Decision type, tool-call mismatch, truncation, refusal, and
  timeout.

## Extensibility

Future Decision types should be added through Platform Specification updates.
Adapters must treat unknown canonical Decision types as unsupported unless they
explicitly advertise support for the version or extension that defines the
type.

Provider-specific extensions should not appear in canonical Decision payloads
unless the Platform Specification defines an extension envelope, namespacing
rules, validation behavior, and redaction rules.

## Platform Specification Impact

RFC-0004 requires specification work before implementation:

- Add a dedicated Model Protocol chapter or expand the Decisions/Pilots/Models
  chapters with a normative provider-adapter protocol.
- Define the canonical Decision schema as the sole Execution Engine input for
  model intent.
- Define Decision version negotiation between Execution Engine, Pilot, and
  provider adapter.
- Define the Pilot/provider adapter normalization pipeline.
- Split validation responsibilities between Pilot and Execution Engine.
- Define model invocation failure categories, retry accounting, and terminal
  behavior.
- Define provider metadata placement in AgentRun status, Events,
  ExecutionFrame data, and trace.
- Define streaming policy for provider tokens and explain why partial Decisions
  are not streamed to the Execution Engine in the initial contract.
- Update Pilot responsibilities to include provider schema negotiation and
  adapter conformance.
- Update Runtime and API trace projections for provider metadata, parse
  failures, refusals, truncation, and model invocation attempts.
- Add glossary terms for Provider Adapter, Model Protocol, Decision Version,
  Structured Output Mechanism, Provider Metadata, and Model Invocation Failure.

## Risks

- Treating provider-native tool calls as ToolInvocations would bypass the
  Execution Engine and weaken Policy, trace, idempotency, and validation.
- Multiple provider-specific Decision schemas would make provider replacement
  observable to runtime and would erode the platform contract.
- Streaming partial Decisions to the Execution Engine would create ambiguous
  side-effect timing and crash recovery semantics.
- Recording provider reasoning or raw prompts without policy controls could
  leak sensitive data.
- Version negotiation that happens only in prompts could fail silently; it
  needs explicit adapter/runtime compatibility checks.
- Plain text parsing can become nondeterministic unless constrained to strict
  canonical schema validation and deterministic rejection.

## Open Questions

- Should the protocol include explicit reasoning fields, or only externally
  observable Decisions?
- How should prompt rendering communicate the protocol schema to providers with
  different structured-output capabilities?
- Should model refusal be represented as a provider failure, a `fail` Decision,
  or a distinct non-Decision adapter outcome that the Execution Engine maps
  through retry policy?
- Which provider metadata is safe to expose through Events by default, and which
  belongs only in redacted trace or status?
- Should provider adapter versions be part of Model resources, Pilot
  configuration, or runtime implementation metadata?
- What compatibility policy applies when a Pilot supports a newer Decision
  version than the local Execution Engine?
