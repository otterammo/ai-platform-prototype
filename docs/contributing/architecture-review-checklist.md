# Architecture Review Checklist

Every significant PR should answer these questions before merge.

## Resource Ownership

- Which resource changed?
- Which controller owns it?
- Does ownership remain correct?
- Do child resources have clear ownerReferences when controller-owned?
- Are cross-scope references explicit and allowed by the specification?

## Reconciliation

- Does reconciliation remain level-based and idempotent?
- Does the responsible controller update status and observedGeneration?
- Are failures recorded in status and events rather than hidden in process
  state?
- Can repeated reconciliation converge without duplicating side effects?

## Runtime Boundary

- Does runtime remain isolated from admission, scheduling, and reconciliation?
- Is AgentRun still the only executable resource?
- Does runtime consume Context instead of querying Knowledge directly?
- Are artifacts reported as resources when durable output is produced?

## Policy And Safety

- Is policy respected before side effects?
- Are approval-required operations represented through Approval resources?
- Are tool and model operations explicit enough for review and audit?

## Observability

- Are events emitted for material decisions and failures?
- Is trace updated with enough correlation to explain behavior?
- Are conditions and phases meaningful without hiding failure causes?

## Specification And Compatibility

- Is the Platform Specification updated before implementation when the contract
  changes?
- Is backward compatibility preserved for stable resource APIs?
- If compatibility changes, is there a new version or deprecation path?
- Are API updates documented?
- Are CLI updates documented?

## Documentation And Quality

- Is the relevant RFC accepted or linked?
- Is an ADR included when the change is architectural?
- Are tests included for behavior, failure modes, and compatibility risks?
- Have `make check` and any required `pre-commit` checks passed?
