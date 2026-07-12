# Pilots

## Purpose

Pilot defines how an Agent reasons, prompts, routes model calls, handles
fallback, parses model responses, and produces Decisions. Pilot is a platform
concept owned by Agent configuration. It is not the same as Model.

Pilot MUST remain independent of any specific model provider.
Pilot MUST NOT execute Decisions or create Resources.
Pilot MUST NOT own AgentRun execution-loop state, alter execution budgets, or
decide whether infrastructure retries occur.

## Relationship To Agent

Agent owns Pilot configuration. Pilot configuration MAY be embedded in Agent
spec or referenced through a platform-defined extension, but the effective Pilot
for an AgentRun MUST be derivable from the Agent and its admitted resources.

Agent status MAY expose Pilot readiness or Pilot failure when those conditions
affect execution.

## Relationship To Model

Model is an execution backend. Pilot selects, routes, or falls back across
Models. Model SHOULD remain an implementation detail of Pilot from the
perspective of Mission and Fleet authors.

Mission and Fleet SHOULD express desired capability and quality constraints
rather than hard-coding provider-specific model choices. Policy MAY restrict
which Models a Pilot can use.

## Prompt Strategy

Pilot owns prompt strategy. Prompt strategy includes system instruction
composition, role framing, output contract framing, memory usage, and how Context
is presented to the model.

Runtime MAY render prompts according to the Pilot strategy after it receives a
scheduled AgentRun and ready Context. Controllers MUST NOT build runtime prompts.

For iterative execution, the Execution Engine owns the canonical ExecutionFrame
data supplied to Pilot. Pilot owns rendering that data into provider-specific
messages.

## Decision Production

Pilot owns provider adaptation and response parsing. It MUST adapt provider
output into the platform Decision protocol before the Execution Engine
interprets it.

Pilot MUST NOT treat provider-native tool calls as platform actions. Provider
native output is input to Decision production; the Execution Engine owns
Decision validation and interpretation.

When provider output cannot be parsed into a Decision, Pilot MUST return a
structured parse failure to the Execution Engine. Pilot MUST NOT convert
unstructured text into completion unless it satisfies the Decision schema.

## Reasoning Configuration

Pilot MAY define reasoning mode, planning depth, tool-use strategy, validation
behavior, and output review requirements. These settings MUST be
provider-neutral unless explicitly placed inside a provider-specific Model
configuration.

Pilot configuration MAY provide default execution preferences only when the
Execution Engine treats them as inherited defaults subject to AgentRun budget
rules. Pilot configuration MUST NOT silently increase parent-enforced budgets.

## Model Routing

Pilot MAY route requests by task type, cost, latency, capability, policy,
availability, or fallback priority. Routing decisions SHOULD be observable in
events and MAY be reflected in AgentRun status.

If routing cannot select a permitted Model, execution MUST fail or wait with a
condition explaining the unsatisfied constraint.

## Fallback

Pilot MAY define fallback behavior for provider errors, model unavailability,
rate limits, safety refusal, or output validation failure. Fallback MUST honor
policy and MUST NOT retry a denied action with another Model to bypass policy.

Fallback attempts SHOULD be traceable through events.

Fallback MUST preserve the Execution Engine's persisted input frame, attempt
numbers, budget accounting, and retry policy.

## Future Multi-Model Orchestration

Pilot MAY coordinate multiple Models within one AgentRun. Multi-model behavior
MUST still report one AgentRun lifecycle and MUST preserve Context provenance,
policy authorization, and Artifact ownership.
