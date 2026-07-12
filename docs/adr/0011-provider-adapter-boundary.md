# ADR 0011: Provider Adapter Boundary

## Title

Provider Adapter Boundary

## Status

Accepted

## Context

RFC-0004 defines the permanent protocol boundary between language model
providers and the platform's Execution Engine. Providers expose different
request formats, response envelopes, tool-calling protocols, structured-output
features, streaming behavior, metadata, refusal formats, and error models.

If the Execution Engine learns those provider-specific formats, provider
replacement would require execution changes and the canonical Decision protocol
would stop being the stable platform contract.

## Decision

Provider Adapter is the model-provider isolation boundary. Provider Adapters
translate provider-specific responses into canonical Decisions or structured
model invocation failures before the Execution Engine receives them.

Decision remains the provider-neutral protocol owned by the platform. Provider
responses, provider-native tool calls, structured-output payloads, streaming
chunks, refusal formats, and local decoder internals are implementation details
inside Provider Adapters.

Execution Engine never consumes provider-specific formats. It validates and
interprets canonical Decisions, applies policy and budget checks, creates
ToolInvocations, handles retries, and selects terminal AgentRun state.

## Consequences

OpenAI, Anthropic, Gemini, Ollama, local models, and future providers can be
integrated by adding or updating Provider Adapters without changing Execution
Engine semantics.

Provider metadata is recorded as execution history, not as Decision semantics.
Missing metadata is represented as unknown or omitted rather than fabricated.

Provider Adapter implementations must pass shared protocol conformance tests for
every Decision version they advertise.

## Alternatives

Letting the Execution Engine understand provider-native tool calls was rejected
because it would bypass the canonical Decision boundary and risk bypassing
ToolInvocation, Policy, Approval, Events, and trace contracts.

Defining provider-specific Decision schemas was rejected because it would make
provider choice observable to execution logic and weaken provider replacement.

Streaming partial Decisions to the Execution Engine was rejected because it
would create ambiguous side-effect timing and crash recovery semantics.
