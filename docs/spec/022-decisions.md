# Decisions

## Purpose

Decision is the canonical protocol between Model intelligence and Execution
Engine control flow. A Decision is a structured, versioned instruction returned
by a Model through a Pilot and interpreted by the Execution Engine.

Decision represents intent. It is not a Resource. The Execution Engine
determines how Decision intent maps to platform resources, status, events, and
trace.

The platform has four foundational protocols:

- Declarative Resources for desired and observed platform state.
- Events for immutable lifecycle and audit history.
- Decisions for provider-neutral Model intent consumed by the Execution Engine.
- Model Protocol for normalizing provider-specific responses into canonical
  Decisions.

## Participants

Pilot owns prompt construction, model routing, fallback, and Provider Adapter
selection. Pilot MUST NOT execute Decisions and MUST NOT create Resources.

Provider Adapter owns provider-specific request construction, response parsing,
metadata extraction, and canonical Decision production. Provider Adapter MUST
NOT execute Decisions and MUST NOT create Resources.

Model produces provider-specific output that Provider Adapter adapts into the
platform Decision protocol. Model MUST NOT directly create platform Resources.

Execution Engine owns Decision validation, Decision interpretation,
ToolInvocation creation, Policy integration, Observation handling, retries,
iteration, and termination. Execution Engine is the only component that converts
Decisions into platform actions.

## Lifecycle

Decision lifecycle is:

```text
Model
-> Decision
-> Validation
-> Execution Engine
-> Platform Resources
-> Events
-> Trace
```

Decisions are ephemeral protocol messages. They are not persisted as Resources,
do not have `apiVersion`, `kind`, `metadata`, `spec`, or `status`, and do not
participate in resource ownership or garbage collection.

The resulting platform actions MUST be persisted through Resources, status, and
Events. Trace projections SHOULD show Decisions alongside the platform actions
created from them.

## Version 1 Types

Decision `version: "v1"` defines these initial types:

- `invoke_tool`: intent to invoke a Tool operation with structured arguments.
- `complete`: intent to complete the AgentRun and produce final output.
- `fail`: intent to terminate the AgentRun unsuccessfully with a structured
  reason.
- `request_input`: intent to pause execution for external input that the
  Execution Engine can represent through platform state.

The following type names are reserved for future specifications and MUST NOT be
used with implementation-defined semantics in v1:

- `delegate`
- `spawn_agent`
- `wait`
- `retry`
- `cancel`
- `checkpoint`

## Schema

Every Decision MUST be a structured object with:

- `version`: Decision protocol version. For this chapter, the value is `"v1"`.
- `type`: Decision type.

An `invoke_tool` Decision MUST include:

- `tool`: Tool identity.
- `operation`: Tool operation name.
- `arguments`: structured operation arguments object.

Example:

```json
{
  "version": "v1",
  "type": "invoke_tool",
  "tool": "filesystem",
  "operation": "write",
  "arguments": {
    "path": "src/LoginForm.tsx",
    "contents": "..."
  }
}
```

A `complete` Decision MUST include:

- `summary`: required human-readable summary.
- `outputs`: array of output references or descriptors. The array MAY be empty
  only when the Mission permits a no-output result.

Example:

```json
{
  "version": "v1",
  "type": "complete",
  "summary": "Implemented and tested the requested change.",
  "outputs": [
    {
      "type": "workspace-change",
      "ref": "workspace://..."
    }
  ]
}
```

A `fail` Decision MUST include `reason` and `retryable` fields. It MAY include
`message` or other diagnostic fields when Policy permits recording them.

A `request_input` Decision MUST include a persisted prompt, question, or request
object that describes the needed input, expected shape, and reason. If the
platform has no supported input mechanism for the request, the Execution Engine
MUST reject the Decision or fail the AgentRun according to its retry and failure
policy.

Decision payloads MAY include provider-neutral diagnostic fields such as
`rationale` only when Policy permits recording them. Decision payloads MUST NOT
include secrets unless a future secure protocol explicitly permits them.

## Validation

Execution Engine MUST validate each Decision before interpretation. Validation
MUST follow this pipeline:

```text
Provider response
-> Provider Adapter parsing
-> Canonical Decision production
-> Decision schema validation
-> Decision version validation
-> Decision semantic validation
-> Agent capability validation
-> Execution budget validation
-> Policy evaluation where applicable
-> Platform action
```

Failure reasons MUST distinguish:

- `DecisionParseFailed`
- `DecisionValidationFailed`
- `DecisionVersionUnsupported`
- `DecisionTypeUnsupported`
- `CapabilityViolation`
- `ToolArgumentsInvalid`

Execution Engine MUST reject unsupported versions or unsupported types unless an
explicit compatibility adapter exists. Execution Engine MUST reject malformed
Decisions without inferring intent from natural language or provider-specific
payloads.

Invalid Decisions MUST fail deterministically. The Execution Engine MAY invoke
the Pilot again with validation feedback according to AgentRun retry policy and
`maxDecisionFailures`, but it MUST NOT perform side effects from an invalid
Decision and MUST preserve enough status or event data to explain the rejection.

Unknown Decision types MUST be rejected. They MUST NOT be treated as
natural-language completion.

## Interpretation

Decision interpretation is owned by the Execution Engine.

For `invoke_tool`, the Execution Engine validates the Decision and then creates
or resumes exactly one deterministic ToolInvocation resource. The ToolInvocation lifecycle, Policy
authorization, approval waiting, Tool Runtime execution, embedded Observation,
and trace are governed by the ToolInvocation specification.

For `complete`, the Execution Engine validates required Mission outputs,
records output references, creates a final summary Artifact unless explicitly
disabled by the Mission, and then records terminal AgentRun status. Failure to
finalize required outputs transitions the AgentRun to `Failed`.

For `fail`, the Execution Engine validates the Decision and records a terminal
failure status and events.

For `request_input`, the Execution Engine validates the Decision and maps it to
supported platform waiting state, approval/input resources, or deterministic
failure.

Completion MUST be explicit. The Execution Engine MUST NOT infer success from
inactivity, an empty response, absence of a tool request, or natural-language
text.

Decision interpretation MUST NOT bypass Resource admission, Policy, Approval,
Workspace isolation, status ownership, Events, or trace contracts.

## Events

Execution Engine SHOULD emit Decision-related events when those states occur:

- `DecisionRequested`
- `DecisionProduced`
- `DecisionValidated`
- `DecisionRejected`

Decision events SHOULD include:

- `correlationId`
- AgentRun reference
- Decision type
- Decision version
- iteration number
- attempt number
- Model and Pilot identity when available
- budget snapshot when available
- rejection reason when applicable

Decision event payloads SHOULD include enough information for trace
reconstruction without storing secrets or policy-redacted details. Event payloads
MAY contain a redacted Decision summary rather than the full Decision payload.

## Trace

Trace projections SHOULD distinguish Model intent from platform execution.

Example:

```text
Decision
type: invoke_tool
-> ToolInvocation
filesystem.write
-> Observation
-> Decision
type: complete
-> Artifact
```

Trace MUST make it possible to reconstruct the order of Decisions, the platform
resources created from them, policy decisions, Observations, Artifacts, terminal
state, and any rejection or retry.

## Compatibility

Decision protocol versions are independent from Resource `apiVersion` values.
The v1 Decision protocol was introduced in Platform Specification `v1.2.0`, its
execution-loop semantics are refined by Platform Specification `v1.3.0`, and
its provider-normalization boundary is defined by Platform Specification
`v1.4.0`.

Future Decision versions SHOULD remain backward compatible where practical.
Execution Engines MAY reject unsupported Decision versions. Model providers
SHOULD negotiate supported Decision versions through Pilot configuration or
capability matching before AgentRun execution.

Provider-native tool calling or structured-output formats MAY be used behind
Provider Adapters, but the platform Decision protocol remains the canonical
contract observed by the Execution Engine. Provider-specific schemas MUST NOT
leak beyond the Provider Adapter boundary.
