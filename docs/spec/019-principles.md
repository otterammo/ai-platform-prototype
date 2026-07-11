# Architectural Principles

These principles guide specification updates, RFC review, implementation, and
architecture decisions.

## Everything Is A Resource

Platform state should be represented as resources with `apiVersion`, `kind`,
`metadata`, `spec`, and `status`. This gives users, controllers, APIs, and
tools one shared language for intent and observed state.

## Desired State Over Imperative Actions

Users declare outcomes in `spec`; the platform converges toward them. This
makes behavior replayable, auditable, and resilient to repeated reconciliation.

## Controllers Reconcile

Controllers compare desired state with observed state and update child
resources, status, and events. They should be level-based, idempotent, and able
to recover from interrupted work.

## Runtime Executes

Runtime executes scheduled AgentRuns and reports results. It must not own
admission, orchestration decisions, scheduling, or resource reconciliation.

## Policy Governs

Side effects and guarded actions flow through policy before execution. Policy
must be explicit enough for review, traceability, and future enforcement.

## Knowledge Is First-Class

Knowledge, indexes, context, provenance, and freshness are part of the platform
contract. Runtime consumes Context rather than reaching into Knowledge directly.

## Models Are Replaceable

Model providers sit behind Model and Pilot contracts. Platform behavior should
not depend on a single vendor, API surface, or model family.

## Agents Are Persistent

Agents represent durable roles in a Fleet. Execution attempts are AgentRuns, not
Agents, so identity, status, memory relationship, and traceability remain
stable across attempts.

## Execution Is Observable

Controllers and runtime must emit events, update status, and preserve enough
trace data for users to understand what happened and why.

## Tools Are Structured

Runtime executes tools only through structured ToolInvocation resources, Policy
decisions, Tool Runtime contracts, and Observations. Natural-language tool
requests are not an execution contract.

## Resources Own Their Children

Lifecycle, garbage collection, and status aggregation follow explicit ownership
relationships. Children should not outlive parent boundaries unless a retention
policy says so.

## Traceability Is Mandatory

Important platform actions should be explainable through resources, events,
conditions, correlation identifiers, artifacts, and ADR/RFC history.
