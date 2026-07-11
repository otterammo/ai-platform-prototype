# ADR 0008: Decision Protocol Boundary

## Title

Decision Protocol Boundary

## Status

Accepted

## Context

Agentic execution needs a stable contract between Model intelligence and
platform execution. The platform already has Declarative Resources for desired
and observed state, and Events for lifecycle history. Without a third protocol,
model output could collapse directly into platform resource creation, coupling
provider-specific model behavior to the platform API.

The architecture separates responsibilities:

```text
Execution Engine
-> Pilot
-> Model
-> Decision
-> Execution Engine
-> Platform Resources
```

Pilot owns prompting, model routing, provider adaptation, and response parsing.
Execution Engine owns control flow and platform action.

## Decision

Decision is the canonical provider-neutral protocol between intelligence and
execution. A Decision is a structured, versioned instruction returned by a Model
through Pilot and interpreted by the Execution Engine.

Decision is not a Resource. It has no resource envelope, ownership lifecycle,
admission path, reconciliation loop, or garbage collection semantics. The
resulting platform actions are persisted as Resources, status, Events, and trace
projections.

Execution Engine owns Decision validation and interpretation. Models propose
intent by producing Decisions. Pilots adapt provider output into Decisions.
Neither Model nor Pilot creates platform Resources directly.

## Consequences

The model protocol remains decoupled from the platform resource API. The
platform can add future Decision types without allowing model output to bypass
Resource admission, Policy, Approval, Events, or traceability.

Execution Engine implementations must validate Decision version, type, required
fields, and argument structure before taking action. Invalid Decisions become
deterministic execution failures or retries according to AgentRun policy.

Trace and event projections must distinguish model intent from platform
execution. Users should be able to see that an `invoke_tool` Decision led to a
ToolInvocation, Policy evaluation, Tool Runtime execution, embedded Observation,
and later terminal Decision.

## Alternatives

Representing Decision as a Resource was rejected because Decisions are ephemeral
protocol messages, not desired or observed platform state. Making them
Resources would add ownership, admission, retention, and reconciliation
complexity without a demonstrated lifecycle requirement.

Letting Models emit ToolInvocation resources directly was rejected because it
would couple provider protocol details to the platform API and weaken the
Execution Engine boundary.

Letting Pilot own execution was rejected because Pilot should remain stateless
reasoning, prompt, routing, provider adaptation, and response parsing logic.
