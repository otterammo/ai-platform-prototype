# DF-003 Provider Recovery Rerun

## 1. Executive Summary

The provider interoperability and approval-recovery platform defects are fixed.
The unchanged DF-003 workload was rerun in fresh state against `gpt-oss:20b`
and `qwen3:8b`.

Run A normalized native OpenAI-compatible `tool_calls`, began real repository
exploration, created nine ToolInvocations, and recorded provider diagnostics in
trace. It produced no empty Decision failures. Run B delivered a recoverable
approval-rejection Observation exactly once and requested a new Decision without
executing the rejected commit. Neither model completed DF-003; the remaining
failures were model decision-quality limitations, not the two targeted platform
defects.

## 2. Platform Changes

- Normalize one native function call into one canonical `invoke_tool` Decision.
- Accept JSON-string or object arguments and reject malformed, ambiguous,
  unsupported, empty, or multi-call responses deterministically.
- Support documented `<tool>.<operation>`, `<tool>.invoke_tool`, and bare
  `<tool>` mappings when the latter forms carry an explicit
  `arguments.operation`.
- Persist safe provider response metadata in ExecutionFrames and Events.
- Add explicit rejection dispositions `terminate` and `continue`; default is
  `terminate`.
- Convert continued rejection into a terminal denied ToolInvocation and one
  embedded ApprovalRejected Observation, bounded by existing budgets.
- Add engineering prompt guidance without repository-specific paths.

No new platform primitive, runtime, planning agent, concurrent Decision, or
parallel tool-call behavior was added.

## 3. Adapter Normalization Results

Focused tests cover the exact `finish_reason: tool_calls`, empty content, one
function-call shape plus malformed arguments, missing operations, ambiguous
names, multiple calls, empty responses, unsupported call types, and unchanged
canonical content handling.

Run A demonstrated successful live normalization to `filesystem.list`,
`filesystem.read`, and `filesystem.write`. The first accepted call listed `.`
and produced a real ToolInvocation and Observation. Some gpt-oss responses used
invalid or incomplete names; these were rejected by the adapter and retried
without side effects.

## 4. Provider Metadata And Trace Results

ExecutionFrames and provider events exposed provider, model, response ID,
finish reason, latency, token counts, response mode, call count, native call ID,
normalization outcome, AgentRun, frame, model attempt, invocation ID, and
correlation ID. Run A trace showed `responseMode: tool_calls`,
`finishReason: tool_calls`, native call identifiers, and both normalization
success and rejection reasons. Run B showed the canonical `content` path.

Events contain no full prompts, secrets, or native tool arguments. Immutable
Observations and Events are historical outcomes, not current repository state.

## 5. Approval Recovery Results

Run B requested approval for a premature `git.commit` against a clean worktree.
It was rejected with:

```text
--disposition continue
--reason "No changes or review output exist. Inspect the implementation and tests, make a minimal fix, and validate before requesting commit."
```

The original ToolInvocation became `Denied`, never executed, and received one
Observation with `error.reason: ApprovalRejected`, the corrective message,
`payload.disposition: continue`, Approval identity, and rejected tool/operation.
The Observation was delivered in frame 2; frame 3 requested a new Decision.
Tool-failure accounting increased by one while model-failure accounting did not.

The model repeated the unsafe commit request. That second Approval was rejected
with `terminate`, preserving compatibility behavior and terminal fencing.

## 6. Run A Results

- Model: `gpt-oss:20b`
- Namespace: `df003-provider-recovery-gptoss-20260714-220707`
- Correlation ID: `91821e83-0f53-42eb-bb00-563be976ff93`
- Fresh platform state: `/tmp/df003-provider-recovery-gptoss3-U8I0WH`
- Final observed state: waiting for approval on a guarded filesystem write
- Usage: 11 iterations, 13 model invocations, 9 ToolInvocations, 2 Decision
  failures, 0 tool failures

The model explored with accepted list/read operations before proposing a write.
There were no empty `rawDecision` failures. Invalid model outputs included an
unauthorized `assistant` tool and a write missing required content; the platform
rejected both safely. The final write remained pending and unexecuted.

## 7. Run B Results

- Model: `qwen3:8b`
- Namespace: `df003-provider-recovery-qwen-20260714-220850`
- Correlation ID: `34cd3d0e-293b-4e56-89c6-fc781b04d9df`
- Fresh platform state: `/tmp/df003-provider-recovery-qwen-WaBviP`
- Final state: Failed after explicit terminal rejection of the repeated action
- Usage: 3 iterations, 4 model invocations, 3 ToolInvocations, 0 Decision
  failures, 1 tool failure

The model first ran `git.status`, then requested a commit while the worktree was
clean. After receiving the continued-rejection Observation, it requested another
commit with nonexistent paths instead of inspecting or editing files. The
second action was terminated explicitly. No commit or file change occurred.

## 8. Decision Quality

- Platform defect fixed: provider-native calls no longer collapse into empty
  Decisions.
- Expected safety behavior: both guarded actions paused before side effects.
- Model limitation, gpt-oss: inconsistent native function naming and several
  malformed Decisions, followed by a broad overwrite proposal.
- Model limitation, qwen: ignored explicit rejection feedback and repeated a
  premature commit.
- Workload issue: none identified; the same benchmark and knowledge inputs were
  used.

## 9. ToolInvocation Sequence

Run A accepted eight exploratory filesystem list/read invocations, then created
one approval-blocked write. Run B created `git.status`, denied the first
`git.commit`, then created a distinct second `git.commit`. Deterministic
identities were preserved; the denied invocation was not replayed and no
duplicate frame was created.

## 10. Validation Results

Focused adapter and approval tests passed three consecutive runs. Full repo
verification passed before the reruns and was repeated after documentation and
dogfood reporting:

- `make fmt`
- `make lint`
- `make typecheck`
- `make test`
- `make check`
- `pre-commit run --all-files`
- `git diff --check`

The dogfood models did not reach repository-local validation because neither
completed a valid implementation.

## 11. Repository Changes

Platform changes are limited to the provider adapter/result boundary,
ExecutionFrame provider metadata/events, approval service, CLI/API request
surfaces, prompt guidance, tests, specifications, README examples, and this
report. The isolated DF-003 worktree remained clean.

## 12. Commit Outcome

No commit was created in the dogfood repository. HEAD remained:

```text
b6a301b docs: expand benchmark repository overview
```

No platform commit or push was performed, as requested.

## 13. Remaining Defects

- Provider response events currently report token usage per response, while
  aggregate AgentRun token-budget fields remain `Unknown`; budget integration
  is separate work.
- Live gpt-oss responses still produce some invalid native call shapes. The
  adapter rejects these diagnostically, but the model spends retry budget.
- The CLI does not accept an output-format flag on `reject`; this is existing UX
  behavior, not part of the requested recovery contract.

No evidence from this rerun requires a new platform primitive.

## 14. Model Limitations

`gpt-oss:20b` can now explore through normalized native calls, but its call
naming and argument quality are inconsistent. `qwen3:8b` produced valid
canonical JSON but failed to revise its plan after explicit feedback. Neither
model produced tests, implementation, validation, review output, or a justified
commit in this rerun.

## 15. Platform Readiness Score

`7/10` for the targeted platform slice. Provider interoperability,
diagnosability, approval safety, recoverable feedback, accounting, and fencing
worked. End-to-end autonomous engineering remains limited by model behavior and
has not yet demonstrated a successful DF-003 implementation.

## 16. Recommended Next Priorities

1. Add provider token usage to aggregate execution-budget accounting; owner:
   Execution Engine; verify with adapter fixtures and trace-budget assertions.
2. Improve model-facing native tool schema guidance for OpenAI-compatible local
   models; owner: Provider Adapter/Pilot; verify with the unchanged DF-003 Run A.
3. Add a dogfood model that reliably follows rejection feedback as a control;
   owner: dogfood; verify corrected exploration after `continue`.
4. Preserve the current sequential Decision and ToolInvocation boundaries; no
   architectural expansion is justified by this evidence.
