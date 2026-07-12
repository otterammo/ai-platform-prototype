# Dogfood Findings

## Awkward Steps

- Environment variables were easy to lose between shells. When `AI_PLATFORM_DB` and `AI_PLATFORM_ROOT` were not exported, resources were applied to or read from the wrong store, producing confusing follow-on failures.
- Knowledge source placement was non-obvious. The manifests lived under `dogfood/knowledge/`, but the runtime resolved `knowledge://...` under the Workspace root at `/Users/mattmoore/dev/otterammo/ai-platform-dogfood/knowledge/`.
- A failed Mission needed a fresh generation to retry cleanly after fixing registry or knowledge inputs. Re-applying the same Mission worked, but this was not obvious from the CLI flow.
- The model did not initially satisfy the requested output name. The objective had to be tightened so the complete Decision included an output object named exactly `review`.
- The CLI YAML output is readable enough for humans, but command output can be very large. Filtering events required extra shell/Python snippets.

## Manual AgentRun Substitution

- Manual substitution was required for every file in `dogfood/invocations/`.
- The placeholder `REPLACE_AGENT_RUN` could not be parsed as-is because resource names must be lowercase DNS-ish strings, so validation had to temporarily substitute a lowercase stand-in.
- The successful AgentRun used for manual ToolInvocations was `analyze-dogfood-repository-fleet-maintainer-run-3`.
- Preparing temporary invocation manifests under `/tmp/ai-platform-dogfood-invocations/` worked, but it is an awkward manual bridge between Mission execution and direct ToolInvocation testing.

## Approvals

- Mutating ToolInvocations were appropriately gated: `filesystem.write`, `git.add`, `git.commit`, and `shell.execute`.
- The Artifact write approval was expected after inspection, but it was initially unclear because it appeared during Mission finalization rather than from a user-authored ToolInvocation.
- The Policy also needed to allow `filesystem/use`, `git/use`, and `shell/use`. These internal tool-availability checks were hard to anticipate from the dogfood request and caused the first real AgentRun to fail with `NoMatchingPolicyRule`.
- Approval listings became noisy because old approved requests remained mixed with the current pending approval.

## Trace Readability

- The trace was useful and reconstructed the overall workflow: Mission, Knowledge, Fleet, Agent, model invocation, Artifact, ToolInvocations, Observations, approvals, and completion.
- The trace was verbose and included earlier failed AgentRuns. That history was valuable for debugging but made it harder to focus on the final successful path.
- Repeated policy lines made the trace feel noisy, especially where approval-required actions appeared before and after approval.

## Duplicate Or Noisy Events

- The timeline showed duplicate-looking entries such as AgentRun creation and execution frame preparation.
- Reconciliation emitted many repeated "already matches" and controller start/completed lines. These are useful for auditability but noisy in a manual dogfood readout.
- `KnowledgeIndex.status.data.error` retained a stale earlier error after the index became `Ready`.
- `git.status` Observation was historically accurate at the time it ran, but after `SUMMARY.md` was committed it still showed the earlier untracked `SUMMARY.md` entry in the saved Observation. This is correct immutable Observation behavior, but it can surprise readers comparing final state.

## Argument Contracts

- Shell runtime configuration required inspecting implementation/tests to discover `config.allowedCommands`, `arguments.argv`, `arguments.cwd`, and timeout behavior.
- Shell timeout behavior was subtle: `spec.timeoutSeconds` caps execution, while `arguments.timeoutSeconds` is also accepted by the runtime.
- Git argument shapes were discoverable only from implementation/tests: `git.add` accepts `path` or `paths`; `git.diff` accepts `path` or `paths` plus `staged`; `git.commit` requires `message` and optionally `allowEmpty`.
- The `Mission.spec.inputs` shape required `{ref: knowledge://...}` objects, while `KnowledgeIndex.spec.sources` accepts string shorthand. This inconsistency caused an initial manifest validation fix.
- YAML scalar quoting mattered for commit messages containing colons, for example `"docs: add platform-generated summary"`.

## Follow-Up Candidates

- Add a CLI helper to stamp an AgentRun name into ToolInvocation templates or apply them with an override.
- Add a validation command that checks manifests against the current store and runtime contracts without applying them.
- Document where `knowledge://...` resolves relative to the Workspace root.
- Improve approval listing filters so pending approvals can be shown without approved historical requests.
- Clear stale `status.data.error` when a KnowledgeIndex transitions back to `Ready`.
- Consider trace/timeline filters for a specific AgentRun, ToolInvocation, or final successful path.
