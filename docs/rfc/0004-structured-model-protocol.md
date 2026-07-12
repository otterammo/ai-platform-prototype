# RFC-0004: Structured Model Protocol

## Title

Structured Model Protocol.

## Authors

TBD.

## Status

Accepted.

## Decision Date

2026-07-12.

## Formal Review Conclusion

Accept.

RFC-0004 is accepted as the Platform Specification `v1.4.0` Model Protocol
contract. The accepted architecture centers on Provider Adapters and protocol
normalization. Provider-specific request and response formats are implementation
details that MUST NOT enter the Execution Engine.

This RFC does not implement provider adapters.

## Motivation

The platform owns the Decision protocol. Model providers do not.

Agentic execution depends on a stable contract between language model output and
platform execution. Providers expose different APIs, structured-output modes,
tool-calling formats, streaming behavior, metadata, refusal formats, and error
models. Those differences must not force changes in the Execution Engine.

RFC-0004 defines how provider-specific responses are translated into the
platform's provider-neutral Decision protocol. This contract should remain
stable for years and should allow OpenAI, Anthropic, Gemini, Ollama, local
models, and future providers to integrate without changing Execution Engine
semantics.

## Background

The platform now has these core execution contracts:

```text
Declarative Resources
-> Control Plane
-> AgentRun
-> Execution Engine
-> Decision
-> ToolInvocation
-> Tool Runtime
```

RFC-0004 defines the model boundary:

```text
Execution Engine
-> Pilot
-> Provider Adapter
-> Language Model
-> Provider Response
-> Provider Adapter
-> Canonical Decision
-> Execution Engine
```

The Execution Engine must never understand provider-specific formats. It sees
only canonical Decisions and structured model invocation failures.

## Goals

- Define Provider Adapter as the permanent model-provider isolation boundary.
- Reaffirm that Decision is the canonical provider-neutral model intent
  protocol.
- Define provider-independent Decision production.
- Define validation ownership between Provider Adapter and Execution Engine.
- Define provider metadata capture without coupling metadata to Decision
  semantics.
- Define streaming normalization.
- Define provider and adapter error ownership.
- Define Decision version compatibility.
- Define conformance testing for Provider Adapters.
- Add Platform Specification `v1.4.0` Model Protocol text before
  implementation.

## Non-Goals

This RFC does not:

- Implement OpenAI, Anthropic, Gemini, Ollama, local, or other Provider
  Adapters.
- Change the `Decision v1` schema.
- Add provider-specific Decision schemas.
- Define Tool Runtime behavior.
- Define the complete multi-turn Agent Runtime beyond the Provider Adapter
  boundary.
- Permit provider-native tool calls to bypass ToolInvocation, Policy, Approval,
  Events, or trace contracts.

## Primary Principle

Every provider response is an implementation detail until a Provider Adapter
normalizes it into a canonical Decision or structured model invocation failure.

The Execution Engine consumes:

- Canonical Decisions.
- Structured model invocation failures.
- Provider metadata recorded as execution history.

The Execution Engine does not consume:

- OpenAI tool-call payloads.
- Anthropic tool-use blocks.
- Gemini function-call payloads.
- Ollama JSON response envelopes.
- Local model grammar decoder internals.
- Provider-specific refusal text.
- Provider-native streaming chunks.

## Provider Adapter

Provider Adapter is the runtime-side component that isolates one provider
family, provider protocol, or local model interface from platform execution.

Provider Adapter owns:

- Provider authentication and credential use.
- Provider request construction.
- Prompt and message serialization.
- Structured-output configuration.
- Provider-specific feature use.
- Provider response parsing.
- Provider metadata extraction.
- Provider-native error classification.
- Canonical Decision creation.

Provider Adapter MUST NOT:

- Execute Decisions.
- Invoke tools.
- Create ToolInvocations.
- Evaluate Policy.
- Manage AgentRun execution retries.
- Own AgentRun execution state.
- Mutate platform Resources.
- Select terminal AgentRun state.

Pilot owns provider selection, prompt strategy, routing, fallback policy, and
Provider Adapter invocation. Provider Adapter owns the provider-specific
translation work for the selected Model.

## Canonical Decision

Every provider that succeeds in producing model intent must produce the same
canonical Decision contract before the Execution Engine receives it.

Example `invoke_tool` Decision:

```json
{
  "version": "v1",
  "type": "invoke_tool",
  "tool": "filesystem",
  "operation": "write",
  "arguments": {}
}
```

Example `complete` Decision:

```json
{
  "version": "v1",
  "type": "complete",
  "summary": "Work completed.",
  "outputs": []
}
```

Provider-specific schemas may exist inside Provider Adapters as translation
contracts. They MUST NOT leak beyond the Provider Adapter boundary. The
Execution Engine validates and interprets the canonical Decision schema defined
by the Platform Specification.

## Provider Capability Matrix

Provider capabilities describe which normalization strategies a Provider Adapter
can use. Missing provider capabilities are normalized by the adapter, not by the
Execution Engine.

| Capability | OpenAI | Anthropic | Gemini | Ollama | Local |
| --- | --- | --- | --- | --- | --- |
| JSON mode | Yes | Yes | Yes | Yes | Varies |
| Tool calling | Yes | Yes | Yes | Varies | Varies |
| Structured output API | Yes | Provider-specific | Yes | Varies | Varies |
| Grammar-constrained decoding | No | No | No | Varies | Yes |
| Streaming | Yes | Yes | Yes | Yes | Varies |
| Token usage | Yes | Yes | Yes | Partial | Varies |
| Reasoning metadata | Provider-specific | Provider-specific | Provider-specific | Optional | Optional |
| Finish reason | Yes | Yes | Yes | Varies | Varies |
| Request identifier | Yes | Yes | Yes | Varies | Varies |

Normalization rules:

- If a provider supports a structured-output API, the adapter SHOULD bind that
  API to the canonical Decision schema for the negotiated Decision version.
- If a provider supports native tool calling, the adapter MAY map provider tool
  calls to canonical Decisions. Provider tool calls are not ToolInvocations.
- If a provider supports JSON mode only, the adapter MUST parse exactly one
  canonical Decision object or return a structured parse failure.
- If a provider supports grammar-constrained decoding, the adapter SHOULD use a
  grammar generated from the canonical Decision schema.
- If a provider supports only plain text, deterministic parsing MAY be used as a
  fallback, but ambiguous or invalid output MUST fail instead of being inferred.
- If token usage, reasoning metadata, finish reason, or request identifiers are
  unavailable, metadata MUST be recorded as unknown or omitted according to the
  Platform Specification. The platform MUST NOT fabricate provider metadata.

## Decision Production Lifecycle

Decision production follows this lifecycle:

```text
Execution Engine
-> ExecutionFrame
-> Pilot
-> Provider Adapter
-> Language Model
-> Provider Response
-> Provider Adapter
-> Canonical Decision or ModelInvocationFailure
-> Execution Engine
```

The Execution Engine supplies the canonical ExecutionFrame data. Pilot renders
that data according to strategy and selects the Model and Provider Adapter. The
Provider Adapter performs provider-specific request/response handling and
returns a canonical Decision or structured failure.

The Decision is the only model-intent artifact that crosses into Execution
Engine interpretation.

## Validation Boundary

Provider Adapter validates:

- Credential availability needed to call the provider.
- Provider transport success.
- Provider response completeness.
- Provider-native structured response shape.
- JSON parsing or provider-native schema parsing.
- Provider-native tool/function call shape.
- Whether provider output can be converted into one canonical Decision object.
- Provider metadata extraction and redaction classification.

Execution Engine validates:

- Decision schema.
- Decision version.
- Decision type.
- Decision semantic correctness for the current AgentRun.
- Agent capability and Tool availability.
- Tool operation argument schema.
- Execution budgets.
- Policy and Approval requirements where applicable.

Provider Adapter may use the canonical Decision schema to prevent malformed
Decisions from crossing the boundary, but Execution Engine remains the
authoritative validator before any platform action occurs.

## Streaming

RFC-0004 supports provider streaming as an adapter-internal input mechanism. It
does not stream partial Decisions to the Execution Engine.

Provider streaming chunks MAY be consumed by the Provider Adapter to improve
latency, support cancellation, collect metadata, detect truncation, or build a
complete structured response. A Decision is complete only when the adapter has:

- Received the provider terminal response or terminal stream event.
- Accumulated required provider metadata.
- Parsed exactly one canonical Decision object.
- Classified the result as a valid adapter output or structured failure.

Trace MAY record streaming metadata such as first-token latency, total latency,
chunk count, token usage, finish reason, truncation, cancellation, and provider
request identifier when policy permits. Trace MUST NOT represent partial
provider output as a Decision, and Execution Engine MUST NOT act on partial
provider output.

This avoids ambiguous side-effect timing, duplicate tool invocation risk, and
crash recovery ambiguity.

## Provider Metadata

Provider metadata is execution history. It is not Decision semantics.

Provider metadata SHOULD be recorded in AgentRun status, ExecutionFrame data,
Events, or trace according to redaction policy. It MUST NOT be required for the
Execution Engine to interpret the Decision.

Recommended metadata:

- Platform Model resource identity.
- Provider name.
- Provider model identifier.
- Provider request identifier.
- Provider adapter name and version.
- Structured-output mechanism used.
- Latency.
- First-token latency when streaming.
- Input, output, total, and reasoning token usage when reported.
- Finish reason.
- Refusal or safety category when reported.
- Truncation indicator.
- Retryability hint for provider or transport failures.

Unavailable metadata is unknown. Unknown metadata MUST NOT be fabricated.

## Error Model

Provider and adapter failures must be classified before Execution Engine retry
policy is applied.

| Failure | Owner | Execution Engine input |
| --- | --- | --- |
| Authentication failure | Provider Adapter | `ModelInvocationFailed` with non-retryable provider reason unless credentials may refresh. |
| Provider transport failure | Provider Adapter | `ModelInvocationFailed` with retryability hint. |
| Provider timeout | Provider Adapter | `ModelInvocationTimedOut`. |
| Rate limit or capacity error | Provider Adapter | `ModelInvocationFailed` with retryability hint and provider metadata. |
| Provider refusal | Provider Adapter | `ModelRefusal` structured outcome; Engine applies AgentRun retry/failure policy. |
| Truncated response | Provider Adapter | `DecisionParseFailed` or `ModelInvocationFailed` with truncation metadata. |
| Malformed provider response | Provider Adapter | `DecisionParseFailed` candidate failure. |
| Provider-native schema mismatch | Provider Adapter | `DecisionParseFailed` or adapter validation failure. |
| Canonical Decision schema invalid | Execution Engine | `DecisionValidationFailed`. |
| Unsupported Decision version | Execution Engine | `DecisionVersionUnsupported`. |
| Unsupported Decision type | Execution Engine | `DecisionTypeUnsupported`. |
| Tool or operation unavailable | Execution Engine | `CapabilityViolation`. |
| Invalid Tool arguments | Execution Engine | `ToolArgumentsInvalid`. |
| Tool execution failure | Execution Engine and Tool Runtime | ToolInvocation terminal phase and Observation. |

Provider Adapter failures consume model invocation retry accounting. Canonical
Decision validation failures consume Decision failure accounting. Tool failures
consume ToolInvocation or tool failure accounting. The Execution Engine owns the
final retry and terminal-state decision.

## Versioning And Compatibility

Decision protocol versions are independent from provider API versions, Provider
Adapter versions, Model resource versions, and resource `apiVersion` values.

Compatibility rules:

- Execution Engine declares supported Decision versions.
- Provider Adapter declares supported Decision versions.
- Pilot MUST choose a Provider Adapter and Decision version compatible with the
  Execution Engine before invoking a Model.
- The current accepted Decision version is `v1`.
- Provider Adapter MUST NOT send an unsupported Decision version to the
  Execution Engine after negotiation.
- If the Execution Engine receives an unsupported Decision version, it MUST
  reject it with `DecisionVersionUnsupported` and perform no side effect.
- Backward-compatible Decision extensions MAY add optional fields that older
  engines ignore or reject deterministically.
- Incompatible Decision changes require a new Decision version, for example
  `v2`.
- Provider Adapter compatibility must be tested against every Decision version
  it advertises.

Provider API upgrades should be isolated inside Provider Adapters unless they
change the canonical Decision contract.

## Testing

Every Provider Adapter must pass the same protocol conformance suite for each
Decision version it advertises.

Required conformance tests:

- Valid `invoke_tool` Decision production.
- Valid `complete` Decision production.
- Valid `fail` Decision production.
- `request_input` Decision production when supported.
- Malformed provider response.
- Invalid JSON or invalid provider-native structured output.
- Invalid canonical Decision schema.
- Unsupported Decision version.
- Unsupported Decision type.
- Provider timeout.
- Provider refusal.
- Truncated provider response.
- Missing token usage.
- Missing finish reason.
- Streaming response that produces one complete Decision.
- Provider-native tool call translated to Decision, not ToolInvocation.

Golden Decision fixtures must be provider-independent. Provider-specific fixtures
may exist only to verify adapter translation into those golden canonical
fixtures.

## Required Platform Specification Updates

RFC-0004 acceptance adds Platform Specification `v1.4.0`. Normative updates are
required for:

- A dedicated [Model Protocol](../spec/024-model-protocol.md) chapter.
- Provider Adapter responsibilities and prohibitions.
- Canonical Decision production.
- Provider capability normalization.
- Streaming normalization.
- Provider metadata placement.
- Provider and adapter error ownership.
- Decision version compatibility.
- Provider Adapter conformance testing.
- Glossary terms for Provider Adapter, Provider Response, Decision Production,
  and Model Invocation Failure.

## ADR Review

[ADR 0011: Provider Adapter Boundary](../adr/0011-provider-adapter-boundary.md)
records the permanent architectural decision that provider-specific formats are
isolated behind Provider Adapters and never consumed by the Execution Engine.

## Implementation Scope

Implementation should follow this RFC only after the Platform Specification
`v1.4.0` Model Protocol text is present.

Implementation should include:

- Provider Adapter interface.
- Conformance test harness.
- Provider-independent golden Decision fixtures.
- At least one adapter implementation in a separate implementation slice.

Implementation MUST NOT let provider-native tool calls, response schemas,
streaming chunks, or refusal payloads bypass canonical Decision production.

## Accepted Decisions

- Provider Adapter is the model-provider isolation boundary.
- The platform owns the Decision protocol.
- Every provider normalizes into the same canonical Decision schema.
- Provider-specific schemas and response formats stay inside Provider Adapters.
- Execution Engine never consumes provider-specific response formats.
- Provider metadata is execution history, not Decision semantics.
- Streaming is adapter-internal until one complete Decision or structured
  failure exists.
- Provider Adapter failures and Decision validation failures have separate
  ownership and retry accounting.
- Decision version compatibility is negotiated before model invocation.
- Provider Adapters must pass shared conformance tests.

## Risks

- Provider-native tool calls could be mistaken for platform ToolInvocations if
  adapters do not enforce the boundary.
- Plain text parsing can be nondeterministic unless adapters reject ambiguous
  output.
- Provider metadata can leak sensitive prompts, reasoning, or safety data unless
  redaction policy is applied consistently.
- Streaming partial Decisions would create side-effect timing and crash recovery
  ambiguity.
- Version negotiation that exists only in prompt text can fail silently; it must
  be represented in adapter and Execution Engine compatibility checks.

## Open Questions

None blocking acceptance.

Future RFCs may define:

- Provider-specific adapter implementations.
- A secure protocol for recording richer provider reasoning metadata.
- New Decision versions.
- Public APIs for inspecting model invocation history beyond redacted trace and
  AgentRun status projections.
