# RFC-0003: Built-In Tool Runtime

## Title

Built-In Tool Runtime.

## Authors

TBD.

## Status

Draft.

## Motivation

The Tool Invocation Framework defines how tools are represented and governed,
but software engineering workloads need a small set of concrete tools. The
initial built-in Tool Runtime should cover filesystem, git, and shell
operations without introducing arbitrary plugin loading.

## Background

RFC-0001 defines ToolInvocation, embedded Observation data, Tool contracts, and
the runtime interface for executing one tool operation. RFC-0003 defines the
first built-in Tool contracts that can satisfy that interface.

## Goals

- Define a filesystem Tool Runtime.
- Define a git Tool Runtime.
- Define a shell Tool Runtime.
- Define sandbox, timeout, output, and redaction behavior for each.
- Keep all operations restricted to the owning Workspace.

## Non-Goals

This RFC does not define Execution Engine loop behavior, structured Model
protocol, browser automation, GUI automation, remote repository pushes, package
manager policy, or arbitrary plugin loading.

## Proposed Design

The platform provides built-in Tool definitions whose operations can be used by
ToolInvocation resources after validation and Policy authorization.

### Filesystem

Filesystem operations:

- `read`
- `write`
- `append`
- `list`
- `mkdir`

Filesystem operations must be restricted to the Workspace root. Runtime must
reject absolute paths, path traversal outside the Workspace root, and symlink
escapes.

### Git

Git operations:

- `status`
- `diff`
- `add`
- `commit`
- `branch`

Git push is explicitly out of scope for the initial built-in runtime.

### Shell

Shell operations:

- approved command execution

Shell execution must capture stdout, stderr, exit code, timeout state, command
identity, working directory, and redaction metadata. Shell execution must remain
sandboxed and use a configurable timeout.

## Policy And Safety

Every operation must pass through Policy before execution. Policy inputs should
include Tool name, operation, arguments or redacted argument metadata, Workspace,
AgentRun, risk level, sandbox requirements, and correlation data.

High-risk shell commands and filesystem writes should be deny-by-default or
approval-gated in shared environments.

## Testing

The implementation should include coverage for:

- filesystem read/write/list/mkdir/append
- path traversal rejection
- symlink escape rejection
- git status/diff/add/commit/branch
- shell stdout, stderr, exit code, timeout, and denial
- Observation payload and redaction behavior

## Open Questions

- Which shell commands should be allowed by default?
- Should git commit author identity come from Policy, AgentRun, or runtime
  configuration?
- What output size limits should built-in tools enforce?
