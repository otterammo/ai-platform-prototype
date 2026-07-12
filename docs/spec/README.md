# Platform Specification Index

The Platform Specification is the normative, implementation-agnostic contract
for the AI Platform. It defines resource semantics, controller behavior, runtime
boundaries, APIs, CLI expectations, events, policy, extensibility, principles,
and versioning.

The current specification version is `v1.3.0`. The current stable resource API
group is `ai.platform/v1`.

## Chapters

1. [Introduction](000-introduction.md)
2. [Architecture](001-architecture.md)
3. [Resource Model](002-resource-model.md)
4. [Control Plane](003-control-plane.md)
5. [Workspaces](004-workspaces.md)
6. [Missions](005-missions.md)
7. [Fleets](006-fleets.md)
8. [Agents](007-agents.md)
9. [AgentRuns](008-agent-runs.md)
10. [Pilots](009-pilots.md)
11. [Models](010-models.md)
12. [Knowledge](011-knowledge.md)
13. [Policy](012-policy.md)
14. [Runtime](013-runtime.md)
15. [Events](014-events.md)
16. [API](015-api.md)
17. [CLI](016-cli.md)
18. [Extensibility](017-extensibility.md)
19. [Glossary](018-glossary.md)
20. [Architectural Principles](019-principles.md)
21. [Versioning](020-versioning.md)
22. [Tool Invocations](021-tool-invocations.md)
23. [Decisions](022-decisions.md)
24. [Execution Engine](023-execution-engine.md)

## Change Rules

- Contract changes MUST be made in this directory before implementation.
- Specification text MUST remain independent of any one database, transport,
  runtime process model, model provider, or repository layout.
- Backward-incompatible changes MUST follow the versioning policy.
- Non-normative examples MUST NOT override normative statements.
