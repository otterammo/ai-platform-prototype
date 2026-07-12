# Model Protocol

## Purpose

The Model Protocol defines how provider-specific language model responses become
canonical platform Decisions. It is the permanent boundary between model
providers and the Execution Engine.

The platform owns the Decision protocol. Provider responses are implementation
details until a Provider Adapter normalizes them into a canonical Decision or a
structured model invocation failure.

## Participants

Execution Engine owns AgentRun execution control flow and supplies canonical
ExecutionFrame data to Pilot. It consumes only canonical Decisions and
structured model invocation failures.

Pilot owns provider selection, prompt strategy, model routing, fallback policy,
and Provider Adapter invocation.

Provider Adapter owns provider-specific request construction, provider response
parsing, provider metadata extraction, and canonical Decision production.

Model is the provider backend or local language model endpoint invoked through a
Provider Adapter.

## Provider Adapter

Provider Adapter isolates one provider family, provider protocol, or local model
interface from platform execution.

Provider Adapter MUST own:

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

## Decision Production

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

The Decision is the only model-intent artifact that crosses into Execution
Engine interpretation.

Provider-native tool calls, function calls, structured-output payloads, refusal
payloads, JSON envelopes, streaming chunks, and local decoder internals MUST NOT
enter Execution Engine interpretation directly.

## Canonical Decision Boundary

Every provider that succeeds in producing model intent MUST produce the same
canonical Decision contract for the negotiated Decision version.

Provider-specific schemas MAY exist inside Provider Adapters as translation
contracts. They MUST NOT leak beyond the Provider Adapter boundary.

The Execution Engine validates and interprets the canonical Decision schema
defined in [Decisions](022-decisions.md).

## Provider Capabilities

Provider capabilities describe which normalization strategies a Provider Adapter
can use.

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

Missing capabilities are normalized by the Provider Adapter:

- A structured-output API SHOULD be bound to the canonical Decision schema.
- Native tool calling MAY be translated to canonical Decisions. Native provider
  tool calls are not ToolInvocations.
- JSON mode MUST parse exactly one canonical Decision object or fail.
- Grammar-constrained decoding SHOULD use a grammar generated from the
  canonical Decision schema.
- Plain text parsing MAY be used only as a deterministic fallback. Ambiguous or
  invalid output MUST fail.
- Missing provider metadata MUST be represented as unknown or omitted. The
  platform MUST NOT fabricate metadata.

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

Provider Adapter MAY use the canonical Decision schema to prevent malformed
Decisions from crossing the boundary, but Execution Engine remains the
authoritative validator before any platform action occurs.

## Streaming

Provider streaming is adapter-internal in Platform Specification `v1.4.0`.
Partial Decisions are not streamed to the Execution Engine.

Provider streaming chunks MAY be consumed by the Provider Adapter to improve
latency, support cancellation, collect metadata, detect truncation, or build a
complete structured response.

A Decision is complete only when the Provider Adapter has:

- Received the provider terminal response or terminal stream event.
- Accumulated required provider metadata.
- Parsed exactly one canonical Decision object.
- Classified the result as a valid adapter output or structured failure.

Trace MAY record streaming metadata such as first-token latency, total latency,
chunk count, token usage, finish reason, truncation, cancellation, and provider
request identifier when policy permits. Trace MUST NOT represent partial
provider output as a Decision, and Execution Engine MUST NOT act on partial
provider output.

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

Provider Adapter failures consume model invocation retry accounting. Canonical
Decision validation failures consume Decision failure accounting. Tool failures
consume ToolInvocation or tool failure accounting. The Execution Engine owns the
final retry and terminal-state decision.

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
- Provider Adapter compatibility MUST be tested against every Decision version
  it advertises.

Provider API upgrades SHOULD be isolated inside Provider Adapters unless they
change the canonical Decision contract.

## Conformance Testing

Every Provider Adapter MUST pass the same protocol conformance suite for each
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

Golden Decision fixtures MUST be provider-independent. Provider-specific
fixtures MAY exist only to verify adapter translation into those golden
canonical fixtures.
