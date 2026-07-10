# Pilots

## Purpose

Pilot defines how an Agent reasons, prompts, routes model calls, handles
fallback, and may orchestrate multiple models. Pilot is a platform concept owned
by Agent configuration. It is not the same as Model.

Pilot MUST remain independent of any specific model provider.

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

## Reasoning Configuration

Pilot MAY define reasoning mode, planning depth, tool-use strategy, maximum
steps, validation behavior, and output review requirements. These settings MUST
be provider-neutral unless explicitly placed inside a provider-specific Model
configuration.

## Decision Contract

Pilot decisions during AgentRun execution MUST be structured. A Pilot decision
MUST be either a final response or a ToolInvocation request. Runtime MUST NOT
interpret natural-language model output as an executable tool request.

A ToolInvocation request MUST identify the Tool, operation, and structured
arguments required by the Tool contract. A final response MUST provide the data
needed for runtime to produce required Artifacts or complete the AgentRun.

## Execution Loop

Runtime returns Observations from completed, failed, denied, timed out, or
cancelled ToolInvocations to the Pilot as execution context. Pilot MAY continue
with another ToolInvocation request or return a final response.

Pilot configuration MUST define or inherit termination limits for maximum
iterations, maximum ToolInvocations, maximum effective token budget, and
cancellation behavior. A Pilot MUST NOT depend on an unbounded execution loop.

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

## Future Multi-Model Orchestration

Pilot MAY coordinate multiple Models within one AgentRun. Multi-model behavior
MUST still report one AgentRun lifecycle and MUST preserve Context provenance,
policy authorization, and Artifact ownership.
