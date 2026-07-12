# Models

## Purpose

Model describes a replaceable model backend available to the platform. A Model
defines provider identity, capabilities, limits, and configuration needed by a
Pilot and Provider Adapter to invoke that backend.

Models MUST remain replaceable. Missions, Fleets, and Agents SHOULD avoid
depending on provider-specific behavior unless a provider-specific contract is
explicitly required.

## Scope

Model is cluster-scoped by default. Use of a Model MAY be restricted by
Workspace policy, admission, quotas, credentials, or provider availability.

Model resources MUST NOT contain secret values directly. Secret references MAY
be used if the platform defines a secure provider contract.

## Provider

Model provider identifies the backend family or protocol. Provider configuration
MAY include endpoint, model identifier, credential reference, timeout, sampling
settings, Provider Adapter selection, and provider-specific options.

Provider-specific settings MUST be isolated to Model configuration or
provider-specific extension fields. They MUST NOT leak into Mission semantics.

## Capabilities

Model capabilities describe what the backend can support. Capabilities MAY
include text generation, structured output, tool calling, embedding, vision,
audio, context window, streaming, reasoning mode, safety controls, or latency
class.

Capability matching SHOULD happen before AgentRun execution. A Pilot MUST NOT
select a Model that fails required capability constraints.

Models produce provider output that Provider Adapters normalize into Decisions.
A Model MUST NOT directly create platform Resources, ToolInvocations,
Artifacts, or Events.

## Limits

Model limits SHOULD describe context size, output size, rate limits, cost class,
timeout, concurrency, and provider availability constraints.

Runtime MUST honor effective limits when invoking a Model. If a Context or
request exceeds Model limits, runtime MUST fail, truncate only according to an
explicit Pilot strategy, or select another permitted Model.

## Configuration

Model configuration SHOULD be declarative and inspectable. Defaults MAY be set
by admission. Runtime MAY apply request-level options from Pilot, but those
options MUST remain within admitted Model and policy constraints.

## Selection

Pilot owns Model selection. Controllers MAY resolve compatible Models for an
Agent when capability matching is part of Fleet reconciliation, but runtime MUST
still invoke only Models permitted by the effective Pilot and policy.

Selection decisions SHOULD be recorded in events with provider, Model identity,
reason, and correlation data.

## Replacement

A Model can be replaced by changing routing, capability matching, Provider
Adapter selection, or provider configuration without changing Mission intent.
Replacing a Model MUST preserve Decision compatibility, resource ownership,
policy evaluation, event traceability, and Artifact ownership.
