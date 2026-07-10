# ADR 0004: Policy Engine

## Title

Policy Engine

## Status

Accepted

## Context

Agents can invoke tools, models, filesystem operations, and other side effects.
The platform needs a consistent governance point before runtime actions occur,
with enough state for approvals, events, and traceability.

## Decision

Runtime actions are evaluated by policy before side effects. Policy resources
define allowed, denied, or approval-required operations. Approval records
capture pending and resolved decisions when a guarded operation requires human
or external approval.

## Consequences

Runtime must identify the tool, operation, actor, and relevant resource context
before invoking side effects. Policy decisions must be observable through
status, events, and Approval resources. New tool and runtime integrations need
policy attributes.

## Alternatives

Embedding authorization in each tool provider was rejected because decisions
would be inconsistent and hard to audit. Allowing unrestricted local runtime
actions was rejected because it conflicts with traceability and governance.
