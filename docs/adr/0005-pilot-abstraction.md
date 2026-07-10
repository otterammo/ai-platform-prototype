# ADR 0005: Pilot Abstraction

## Title

Pilot Abstraction

## Status

Accepted

## Context

Agents need reasoning, prompting, routing, fallback, and model selection
behavior without becoming tied to a single model provider. The platform also
needs a place to express provider-independent orchestration policy for an Agent.

## Decision

Pilot is the provider-independent model orchestration abstraction owned by an
Agent. Pilot configuration selects or routes to Model resources while preserving
Agent identity and keeping model providers replaceable.

## Consequences

Agent resources can describe role and execution intent without embedding
provider-specific behavior. Model replacement, routing, and future multi-model
orchestration can evolve behind Pilot and Model contracts.

## Alternatives

Embedding model provider configuration directly in Agent was rejected because
it would couple Agents to vendors and make replacement harder. Making Pilot a
standalone top-level actor was rejected at this stage because Agent ownership is
the clearer boundary for role-specific reasoning policy.
