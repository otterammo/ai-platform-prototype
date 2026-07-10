# RFC-0005: Multi-Turn Agent Runtime

## Title

Multi-Turn Agent Runtime.

## Authors

TBD.

## Status

Draft.

## Motivation

The platform ultimately needs to execute software engineering workloads that
inspect files, make changes, run commands, observe results, and continue until a
final response. That full behavior should be composed only after the lower-level
contracts are defined independently.

## Background

RFC-0005 depends on:

- RFC-0001 Tool Invocation Framework.
- RFC-0002 Pilot Execution Loop.
- RFC-0003 Built-In Tool Runtime.
- RFC-0004 Structured Model Protocol.

Each dependency must be accepted and incorporated into the Platform
Specification before RFC-0005 implementation begins.

Only after those contracts exist should the platform connect:

```text
Pilot
-> Model
-> ToolInvocation
-> Tool
-> Observation
-> Model
-> ...
-> FinalResponse
```

## Goals

- Compose the Pilot loop, structured Model protocol, ToolInvocation framework,
  and built-in Tool Runtimes into one AgentRun execution flow.
- Produce Artifacts after multiple tool invocations.
- Persist modified files as Workspace changes.
- Reconstruct the entire execution through Trace.
- Support realistic software engineering workloads.

## Non-Goals

This RFC does not introduce autonomous planning across multiple Missions,
distributed workers, browser automation, GUI automation, remote git push, or
arbitrary plugin loading.

## Proposed Design

Runtime executes a scheduled AgentRun by loading Ready Context, invoking the
effective Pilot, requesting structured Model decisions, mapping tool decisions
to ToolInvocation resources, executing authorized tools, recording
Observations, continuing the loop, and producing Artifacts when the Model
returns a final response.

Runtime must remain inside the AgentRun boundary. It must not schedule work,
reconcile resources, perform admission, build Context from Knowledge, or mutate
Mission, Fleet, or Agent spec.

## Acceptance Criteria

The platform should successfully execute a Mission such as:

> Implement a login page in the sample application.

Expected behavior:

1. Retrieve Context.
2. Ask the Model for the next structured decision.
3. Receive a structured ToolInvocation request.
4. Authorize the invocation through Policy.
5. Execute the Tool.
6. Record an Observation.
7. Continue the execution loop.
8. Repeat until the Model returns a final response.
9. Persist modified files as Workspace changes.
10. Produce Artifact resources summarizing completed work.
11. Reconstruct the entire execution through Trace.

## Testing

The implementation should include coverage for:

- structured tool requests
- filesystem read/write
- git operations
- shell execution
- policy approval
- denied tool requests
- observation generation
- iterative execution
- retry behavior
- loop termination
- trace reconstruction
- artifact generation after multiple tool invocations

Include an end-to-end workload that modifies a real sample repository inside a
temporary Workspace and verifies resulting file changes.

## Open Questions

- What minimum model capability should be required for the first end-to-end
  workload?
- Should the first implementation allow only built-in tools?
- How should Workspace file changes be represented as Artifacts versus ordinary
  repository diffs?
