# ADR 0002: AgentRun Execution Boundary

## Title

AgentRun Execution Boundary

## Status

Accepted

## Context

The platform needs to distinguish durable agent identity from execution
attempts. Without a single executable resource boundary, controllers, runtime,
policy, retries, artifacts, and events can overlap in ways that make behavior
hard to audit.

## Decision

AgentRun is the only executable resource. Agents are persistent role-bearing
resources owned by Fleets. Runtime executes scheduled AgentRuns, consumes
Context, evaluates policy before side effects, writes artifacts, and reports
status and events.

## Consequences

Controllers create and schedule AgentRuns but do not perform runtime side
effects. Runtime does not reconcile resources or make orchestration decisions.
Retry, approval, trace, and artifact semantics attach to AgentRun attempts.

## Alternatives

Executing Agents directly was rejected because it conflates identity with
attempts. Letting arbitrary resources execute was rejected because it weakens
policy, scheduling, observability, and runtime isolation.
