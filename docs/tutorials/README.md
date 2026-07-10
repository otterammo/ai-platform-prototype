# Tutorials

Start here if you are new to the AI Platform.

This tutorial is the canonical Day 0 path. It uses the local stub model, so it
does not require network access or an API key. You will install the platform,
create a Workspace, index Knowledge, apply a Mission, approve a guarded action,
inspect trace and artifacts, and clean up the resources.

## Day 0: First Mission

Scenario: implement a login page.

```text
Platform
`-- Workspace
    `-- Knowledge
    `-- Mission
        `-- Fleet
            `-- Agent
                `-- AgentRun
                    |-- Context
                    `-- Artifact
```

Follow the chapters in order:

1. [Installation](00-installation.md)
2. [First Workspace](01-first-workspace.md)
3. [First Knowledge](02-first-knowledge.md)
4. [First Mission](03-first-mission.md)
5. [Observing The Platform](04-observing-the-platform.md)
6. [Approvals](05-approvals.md)
7. [Artifacts](06-artifacts.md)
8. [Cleanup](07-cleanup.md)

## Prepare The Tutorial Files

From the repository root:

```bash
mkdir -p day0
cp -R docs/tutorials/assets/day0/. day0/
```

The rest of the tutorial assumes these defaults:

```bash
export AI_PLATFORM_DB=sqlite:///./platform.db
export AI_PLATFORM_ROOT=.platform
```

The checked-in tutorial assets are also used by the automated Day 0 acceptance
test.
