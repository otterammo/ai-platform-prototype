---
name: platform-release
description: Prepare AI Platform release readiness, release notes, changelog entries, version updates, RFC status, ADR status, roadmap updates, compatibility notes, and merge readiness.
---

# Platform Release

Use this skill when preparing a release, changelog entry, or merge readiness
review.

## Checklist

- Included RFCs have correct status.
- Required ADRs are accepted or documented as not applicable.
- Platform Specification version and resource API compatibility are correct.
- Roadmap reflects landed behavior.
- Tutorials and examples still run.
- Dogfood reports are complete for release-critical features.
- Changelog or release notes describe user-visible behavior.
- Migration and deprecation notes exist when compatibility changes.
- `make check` and required `pre-commit` checks passed.

## Release Note Template

```md
# Release Note

## User-Visible Changes

## Compatibility

## Governance

- RFC:
- ADR:
- Specification:
- Roadmap:

## Verification
```

## Output

Report blockers, release-note text, and any required follow-up before declaring
the work ready.
