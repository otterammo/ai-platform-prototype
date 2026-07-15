from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar

from .events import CORRELATION_ID_ANNOTATION, CORRELATION_ID_STATUS_KEY, EventContext
from .models import Message, ModelClient, build_model_client
from .policy import ApprovalRequired, PolicyDenied, PolicyEngine, RuntimeAction
from .resources import (
    AgentResource,
    AgentRunResource,
    ContextResource,
    ExecutionSpec,
    MissionResource,
    ModelConfig,
    ModelResource,
    Observation,
    ObservationError,
    ResourceKind,
    ToolInvocationResource,
    ToolOperationSpec,
    ToolResource,
    WorkspaceResource,
    parse_resource,
)
from .storage import CONTROLLER_FIELD_MANAGER, ResourceStore

_ResourceT = TypeVar("_ResourceT")
ModelClientFactory = Callable[[ModelConfig, ResourceStore | None], ModelClient]

AGENT_RUN_TERMINAL_PHASES = {"Succeeded", "Failed", "Cancelled", "TimedOut", "BudgetExceeded"}
ENGINE_RESUMABLE_PHASES = {
    "Scheduled",
    "Starting",
    "AwaitingDecision",
    "DecisionReady",
    "ProcessingDecision",
    "WaitingForTool",
    "WaitingForObservation",
    "Finalizing",
}
TOOL_TERMINAL_PHASES = {"Succeeded", "Failed", "Denied", "TimedOut", "Cancelled"}
EXECUTION_OWNER = "local"
EXECUTION_EPOCH_ANNOTATION = "ai.platform/execution-epoch"
EXECUTION_FRAME_INDEX_ANNOTATION = "ai.platform/execution-frame-index"


class ToolRuntimeError(Exception):
    pass


class ToolOperationError(Exception):
    def __init__(self, reason: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self.reason = reason
        self.message = message
        self.payload = payload or {}
        super().__init__(message)


class ToolRuntime(Protocol):
    runtime_id: str

    def execute(self, invocation: ToolInvocationResource) -> Observation: ...


class FakeToolRuntime:
    runtime_id = "builtin.fake"

    def execute(self, invocation: ToolInvocationResource) -> Observation:
        if invocation.spec.tool != "fake" or invocation.spec.operation != "echo":
            raise ToolRuntimeError(f"fake runtime cannot execute {invocation.spec.tool}.{invocation.spec.operation}")
        return Observation(
            summary="Echo completed",
            payload={"message": invocation.spec.arguments.get("message")},
        )


class WorkspaceToolRuntime:
    def __init__(self, store: ResourceStore) -> None:
        self.store = store

    def _workspace(self, invocation: ToolInvocationResource) -> WorkspaceResource:
        workspace_name = invocation.metadata.namespace
        if workspace_name is None:
            raise ToolRuntimeError("ToolInvocation must name a Workspace")
        manifest = self.store.get(ResourceKind.WORKSPACE, workspace_name)
        if manifest is None:
            raise ToolRuntimeError(f"Workspace {workspace_name} not found")
        workspace = parse_resource(manifest)
        if not isinstance(workspace, WorkspaceResource):
            raise TypeError(f"expected WorkspaceResource, got {type(workspace).__name__}")
        return workspace

    def _workspace_root(self, invocation: ToolInvocationResource) -> Path:
        workspace = self._workspace(invocation)
        return workspace.spec.resolved_root(self.store.platform_root, workspace.metadata.name).resolve(strict=False)

    def _tool_config(self, invocation: ToolInvocationResource) -> dict[str, Any]:
        manifest = self.store.get(ResourceKind.TOOL, invocation.spec.tool)
        if manifest is None:
            return {}
        tool = parse_resource(manifest)
        if not isinstance(tool, ToolResource):
            return {}
        return dict(tool.spec.config)

    def _resolve_path(self, workspace_root: Path, raw_path: Any, *, allow_root: bool = False) -> tuple[Path, str]:
        if not isinstance(raw_path, str):
            raise ToolOperationError("InvalidPath", "path must be a string")
        if not raw_path:
            raise ToolOperationError("InvalidPath", "path must not be empty")
        if "\\" in raw_path:
            raise ToolOperationError("InvalidPath", "path must use slash separators")
        if raw_path == ".":
            if not allow_root:
                raise ToolOperationError("InvalidPath", "path must identify a file or directory below workspace root")
            relative_path = Path(".")
        else:
            parts = raw_path.split("/")
            if any(part in {"", ".", ".."} for part in parts):
                reason = "PathTraversalDenied" if ".." in parts else "InvalidPath"
                raise ToolOperationError(reason, "path must be normalized and workspace-relative", {"path": raw_path})
            relative_path = Path(raw_path)
        if relative_path.is_absolute():
            raise ToolOperationError("InvalidPath", "path must be workspace-relative", {"path": raw_path})
        resolved = (workspace_root / relative_path).resolve(strict=False)
        try:
            resolved.relative_to(workspace_root)
        except ValueError as exc:
            raise ToolOperationError(
                "PathTraversalDenied",
                "path resolves outside workspace",
                {"path": raw_path},
            ) from exc
        display_path = "." if relative_path == Path(".") else relative_path.as_posix()
        return resolved, display_path

    def _resolve_optional_paths(self, workspace_root: Path, arguments: dict[str, Any]) -> list[str]:
        raw_paths = arguments.get("paths")
        raw_path = arguments.get("path")
        if raw_paths is None and raw_path is None:
            return []
        if raw_paths is None:
            raw_paths = [raw_path]
        if not isinstance(raw_paths, list) or not raw_paths:
            raise ToolOperationError("InvalidPath", "paths must be a non-empty array")
        paths: list[str] = []
        for item in raw_paths:
            _resolved, display_path = self._resolve_path(workspace_root, item)
            paths.append(display_path)
        return paths

    @staticmethod
    def _error_observation(error: ToolOperationError) -> Observation:
        return Observation(
            summary=error.message,
            payload=error.payload,
            error=ObservationError(reason=error.reason, message=error.message),
        )

    @staticmethod
    def _os_error(reason: str, path: str, exc: OSError) -> Observation:
        message = f"{reason}: {path}"
        return Observation(
            summary=message,
            payload={"path": path, "errno": exc.errno},
            error=ObservationError(reason=reason, message=message),
        )

    @staticmethod
    def _output_text(value: str | bytes | None) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value


class FilesystemToolRuntime(WorkspaceToolRuntime):
    runtime_id = "builtin.filesystem"

    def execute(self, invocation: ToolInvocationResource) -> Observation:
        try:
            workspace_root = self._workspace_root(invocation)
            operation = invocation.spec.operation
            if operation == "read":
                return self._read(workspace_root, invocation.spec.arguments)
            if operation == "write":
                return self._write(workspace_root, invocation.spec.arguments)
            if operation == "append":
                return self._append(workspace_root, invocation.spec.arguments)
            if operation == "mkdir":
                return self._mkdir(workspace_root, invocation.spec.arguments)
            if operation == "list":
                return self._list(workspace_root, invocation.spec.arguments)
            if operation == "exists":
                return self._exists(workspace_root, invocation.spec.arguments)
            if operation == "stat":
                return self._stat(workspace_root, invocation.spec.arguments)
            raise ToolOperationError(
                "UnsupportedOperation",
                f"filesystem runtime cannot execute operation {operation}",
                {"operation": operation},
            )
        except ToolOperationError as exc:
            return self._error_observation(exc)

    def _read(self, workspace_root: Path, arguments: dict[str, Any]) -> Observation:
        path, display_path = self._resolve_path(workspace_root, arguments.get("path"))
        if not path.exists():
            raise ToolOperationError("PathNotFound", f"path not found: {display_path}", {"path": display_path})
        if not path.is_file():
            raise ToolOperationError("InvalidPath", f"path is not a file: {display_path}", {"path": display_path})
        try:
            content = path.read_text(encoding="utf-8")
        except PermissionError as exc:
            return self._os_error("PermissionDenied", display_path, exc)
        return Observation(
            summary=f"Read {display_path}",
            payload={"path": display_path, "content": content, "bytes": len(content.encode("utf-8"))},
        )

    def _write(self, workspace_root: Path, arguments: dict[str, Any]) -> Observation:
        path, display_path = self._resolve_path(workspace_root, arguments.get("path"))
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolOperationError("InvalidArguments", "content must be a string", {"path": display_path})
        overwrite = arguments.get("overwrite", True)
        if not isinstance(overwrite, bool):
            raise ToolOperationError("InvalidArguments", "overwrite must be a boolean", {"path": display_path})
        if path.exists() and path.is_dir():
            raise ToolOperationError("InvalidPath", f"path is a directory: {display_path}", {"path": display_path})
        if path.exists() and not overwrite:
            raise ToolOperationError("PathExists", f"path already exists: {display_path}", {"path": display_path})
        if not path.parent.exists():
            raise ToolOperationError(
                "PathNotFound",
                f"parent directory not found: {Path(display_path).parent.as_posix()}",
                {"path": display_path},
            )
        existed = path.exists()
        try:
            path.write_text(content, encoding="utf-8")
        except PermissionError as exc:
            return self._os_error("PermissionDenied", display_path, exc)
        return Observation(
            summary=f"Wrote {display_path}",
            payload={
                "path": display_path,
                "bytes": len(content.encode("utf-8")),
                "overwritten": existed,
            },
        )

    def _append(self, workspace_root: Path, arguments: dict[str, Any]) -> Observation:
        path, display_path = self._resolve_path(workspace_root, arguments.get("path"))
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ToolOperationError("InvalidArguments", "content must be a string", {"path": display_path})
        if path.exists() and path.is_dir():
            raise ToolOperationError("InvalidPath", f"path is a directory: {display_path}", {"path": display_path})
        if not path.parent.exists():
            raise ToolOperationError(
                "PathNotFound",
                f"parent directory not found: {Path(display_path).parent.as_posix()}",
                {"path": display_path},
            )
        created = not path.exists()
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(content)
        except PermissionError as exc:
            return self._os_error("PermissionDenied", display_path, exc)
        return Observation(
            summary=f"Appended {display_path}",
            payload={"path": display_path, "bytes": len(content.encode("utf-8")), "created": created},
        )

    def _mkdir(self, workspace_root: Path, arguments: dict[str, Any]) -> Observation:
        path, display_path = self._resolve_path(workspace_root, arguments.get("path"))
        parents = arguments.get("parents", True)
        exist_ok = arguments.get("existOk", True)
        if not isinstance(parents, bool) or not isinstance(exist_ok, bool):
            raise ToolOperationError("InvalidArguments", "parents and existOk must be booleans", {"path": display_path})
        try:
            path.mkdir(parents=parents, exist_ok=exist_ok)
        except FileExistsError as exc:
            return self._os_error("PathExists", display_path, exc)
        except PermissionError as exc:
            return self._os_error("PermissionDenied", display_path, exc)
        return Observation(summary=f"Created directory {display_path}", payload={"path": display_path})

    def _list(self, workspace_root: Path, arguments: dict[str, Any]) -> Observation:
        path, display_path = self._resolve_path(workspace_root, arguments.get("path", "."), allow_root=True)
        if not path.exists():
            raise ToolOperationError("PathNotFound", f"path not found: {display_path}", {"path": display_path})
        if not path.is_dir():
            raise ToolOperationError("InvalidPath", f"path is not a directory: {display_path}", {"path": display_path})
        entries = [
            self._entry_payload(workspace_root, item) for item in sorted(path.iterdir(), key=lambda item: item.name)
        ]
        return Observation(summary=f"Listed {display_path}", payload={"path": display_path, "entries": entries})

    def _exists(self, workspace_root: Path, arguments: dict[str, Any]) -> Observation:
        path, display_path = self._resolve_path(workspace_root, arguments.get("path"), allow_root=True)
        exists = path.exists()
        payload: dict[str, Any] = {"path": display_path, "exists": exists}
        if exists:
            payload["type"] = self._path_type(path)
        return Observation(summary=f"Checked {display_path}", payload=payload)

    def _stat(self, workspace_root: Path, arguments: dict[str, Any]) -> Observation:
        path, display_path = self._resolve_path(workspace_root, arguments.get("path"), allow_root=True)
        if not path.exists():
            raise ToolOperationError("PathNotFound", f"path not found: {display_path}", {"path": display_path})
        payload = self._entry_payload(workspace_root, path)
        payload["permissions"] = {
            "readable": os.access(path, os.R_OK),
            "writable": os.access(path, os.W_OK),
            "executable": os.access(path, os.X_OK),
        }
        return Observation(summary=f"Statted {display_path}", payload=payload)

    def _entry_payload(self, workspace_root: Path, path: Path) -> dict[str, Any]:
        relative_path = "." if path == workspace_root else path.relative_to(workspace_root).as_posix()
        stat_result = path.lstat()
        return {
            "name": path.name,
            "path": relative_path,
            "type": self._path_type(path),
            "size": stat_result.st_size,
            "mode": oct(stat_result.st_mode & 0o777),
            "modifiedTime": datetime.fromtimestamp(stat_result.st_mtime, UTC).isoformat(),
        }

    @staticmethod
    def _path_type(path: Path) -> str:
        if path.is_symlink():
            return "symlink"
        if path.is_dir():
            return "directory"
        if path.is_file():
            return "file"
        return "other"


class GitToolRuntime(WorkspaceToolRuntime):
    runtime_id = "builtin.git"

    def execute(self, invocation: ToolInvocationResource) -> Observation:
        try:
            workspace_root = self._workspace_root(invocation)
            repo_error = self._workspace_repo_error(workspace_root, invocation)
            if repo_error is not None:
                return repo_error
            operation = invocation.spec.operation
            if operation == "status":
                return self._status(workspace_root, invocation)
            if operation == "diff":
                return self._diff(workspace_root, invocation)
            if operation == "add":
                return self._add(workspace_root, invocation)
            if operation == "commit":
                return self._commit(workspace_root, invocation)
            if operation == "branch":
                return self._branch(workspace_root, invocation)
            raise ToolOperationError(
                "UnsupportedOperation",
                f"git runtime cannot execute operation {operation}",
                {"operation": operation},
            )
        except ToolOperationError as exc:
            return self._error_observation(exc)

    def _status(self, workspace_root: Path, invocation: ToolInvocationResource) -> Observation:
        status = self._run_git(workspace_root, ["git", "status", "--porcelain=v1", "--untracked-files=all"], invocation)
        if status.error is not None:
            return status
        branch = self._run_git(workspace_root, ["git", "branch", "--show-current"], invocation)
        branch_name = "" if branch.error else str(branch.payload.get("stdout", "")).strip()
        entries = [line for line in str(status.payload.get("stdout", "")).splitlines() if line]
        return Observation(
            summary="Git status completed",
            payload={"branch": branch_name, "clean": not entries, "entries": entries},
        )

    def _diff(self, workspace_root: Path, invocation: ToolInvocationResource) -> Observation:
        staged = invocation.spec.arguments.get("staged", False)
        if not isinstance(staged, bool):
            raise ToolOperationError("InvalidArguments", "staged must be a boolean")
        command = ["git", "diff"]
        if staged:
            command.append("--staged")
        paths = self._resolve_optional_paths(workspace_root, invocation.spec.arguments)
        if paths:
            command.extend(["--", *paths])
        return self._run_git(workspace_root, command, invocation, success_summary="Git diff completed")

    def _add(self, workspace_root: Path, invocation: ToolInvocationResource) -> Observation:
        paths = self._resolve_optional_paths(workspace_root, invocation.spec.arguments)
        if not paths:
            raise ToolOperationError("InvalidPath", "git add requires path or paths")
        result = self._run_git(
            workspace_root,
            ["git", "add", "--", *paths],
            invocation,
            success_summary="Git add completed",
        )
        if result.error is None:
            result.payload["paths"] = paths
        return result

    def _commit(self, workspace_root: Path, invocation: ToolInvocationResource) -> Observation:
        message = invocation.spec.arguments.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ToolOperationError("InvalidArguments", "git commit requires a non-empty message")
        allow_empty = invocation.spec.arguments.get("allowEmpty", False)
        if not isinstance(allow_empty, bool):
            raise ToolOperationError("InvalidArguments", "allowEmpty must be a boolean")
        command = ["git", "commit", "-m", message]
        if allow_empty:
            command.append("--allow-empty")
        result = self._run_git(workspace_root, command, invocation, success_summary="Git commit completed")
        if result.error is not None:
            return result
        commit = self._run_git(workspace_root, ["git", "rev-parse", "HEAD"], invocation)
        if commit.error is None:
            result.payload["commit"] = str(commit.payload.get("stdout", "")).strip()
        return result

    def _branch(self, workspace_root: Path, invocation: ToolInvocationResource) -> Observation:
        name = invocation.spec.arguments.get("name")
        if name is not None:
            if not isinstance(name, str) or not name.strip() or name.startswith("-"):
                raise ToolOperationError("InvalidArguments", "branch name must be a non-option string")
            created = self._run_git(
                workspace_root,
                ["git", "branch", name],
                invocation,
                success_summary="Git branch created",
            )
            if created.error is not None:
                return created
        current = self._run_git(workspace_root, ["git", "branch", "--show-current"], invocation)
        branches = self._run_git(workspace_root, ["git", "branch", "--format=%(refname:short)"], invocation)
        if current.error is not None:
            return current
        if branches.error is not None:
            return branches
        branch_names = [line for line in str(branches.payload.get("stdout", "")).splitlines() if line]
        return Observation(
            summary="Git branch completed",
            payload={"current": str(current.payload.get("stdout", "")).strip(), "branches": branch_names},
        )

    def _workspace_repo_error(self, workspace_root: Path, invocation: ToolInvocationResource) -> Observation | None:
        command = ["git", "rev-parse", "--show-toplevel"]
        timeout_seconds = invocation.spec.timeoutSeconds
        try:
            completed = subprocess.run(
                command,
                cwd=workspace_root,
                env=self._git_env(workspace_root),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            timeout_payload: dict[str, Any] = {
                "command": command,
                "stdout": self._output_text(exc.stdout),
                "stderr": self._output_text(exc.stderr),
                "exitCode": None,
                "timedOut": True,
            }
            return Observation(
                summary="Git repository check timed out",
                payload=timeout_payload,
                error=ObservationError(
                    reason="GitRepositoryCheckTimedOut",
                    message="Git repository check timed out",
                ),
            )
        check_payload: dict[str, Any] = {
            "command": command,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exitCode": completed.returncode,
            "timedOut": False,
        }
        if completed.returncode != 0:
            message = "Workspace root is not a Git repository"
            return Observation(
                summary=message,
                payload=check_payload,
                error=ObservationError(reason="GitRepositoryInvalid", message=message),
            )
        top_level_text = completed.stdout.strip()
        top_level = Path(top_level_text).expanduser().resolve(strict=False)
        if top_level != workspace_root:
            message = "Git repository top-level must match workspace root"
            return Observation(
                summary=message,
                payload={
                    **check_payload,
                    "workspaceRoot": str(workspace_root),
                    "gitTopLevel": str(top_level),
                },
                error=ObservationError(reason="GitRepositoryEscaped", message=message),
            )
        return None

    def _run_git(
        self,
        workspace_root: Path,
        command: list[str],
        invocation: ToolInvocationResource,
        *,
        success_summary: str = "Git command completed",
    ) -> Observation:
        timeout_seconds = invocation.spec.timeoutSeconds
        try:
            completed = subprocess.run(
                command,
                cwd=workspace_root,
                env=self._git_env(workspace_root),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            timeout_payload: dict[str, Any] = {
                "command": command,
                "stdout": self._output_text(exc.stdout),
                "stderr": self._output_text(exc.stderr),
                "exitCode": None,
                "timedOut": True,
            }
            return Observation(
                summary="Git command timed out",
                payload=timeout_payload,
                error=ObservationError(reason="GitCommandTimedOut", message="Git command timed out"),
            )
        command_payload: dict[str, Any] = {
            "command": command,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exitCode": completed.returncode,
            "timedOut": False,
        }
        if completed.returncode != 0:
            message = f"Git command failed with exit code {completed.returncode}"
            return Observation(
                summary=message,
                payload=command_payload,
                error=ObservationError(reason="GitCommandFailed", message=message),
            )
        return Observation(summary=success_summary, payload=command_payload)

    @staticmethod
    def _git_env(workspace_root: Path) -> dict[str, str]:
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": "AI Platform",
            "GIT_AUTHOR_EMAIL": "ai-platform@example.invalid",
            "GIT_COMMITTER_NAME": "AI Platform",
            "GIT_COMMITTER_EMAIL": "ai-platform@example.invalid",
            "GIT_CEILING_DIRECTORIES": str(workspace_root.parent),
        }


class ShellToolRuntime(WorkspaceToolRuntime):
    runtime_id = "builtin.shell"

    def execute(self, invocation: ToolInvocationResource) -> Observation:
        try:
            if invocation.spec.operation != "execute":
                raise ToolOperationError(
                    "UnsupportedOperation",
                    f"shell runtime cannot execute operation {invocation.spec.operation}",
                    {"operation": invocation.spec.operation},
                )
            workspace_root = self._workspace_root(invocation)
            config = self._tool_config(invocation)
            argv = self._argv(invocation.spec.arguments)
            allowed_commands = config.get("allowedCommands") or []
            if not isinstance(allowed_commands, list) or any(not isinstance(item, str) for item in allowed_commands):
                raise ToolOperationError("InvalidConfiguration", "allowedCommands must be an array of strings")
            self._validate_allowed_command(argv[0], allowed_commands)
            cwd, cwd_display = self._resolve_path(
                workspace_root,
                invocation.spec.arguments.get("cwd", "."),
                allow_root=True,
            )
            if not cwd.is_dir():
                raise ToolOperationError(
                    "InvalidWorkingDirectory",
                    f"working directory is not a directory: {cwd_display}",
                    {"cwd": cwd_display},
                )
            return self._run_command(invocation, argv, cwd, cwd_display)
        except ToolOperationError as exc:
            return self._error_observation(exc)

    @staticmethod
    def _argv(arguments: dict[str, Any]) -> list[str]:
        raw_argv = arguments.get("argv")
        raw_command = arguments.get("command")
        if raw_argv is not None:
            if (
                not isinstance(raw_argv, list)
                or not raw_argv
                or any(not isinstance(item, str) or not item for item in raw_argv)
            ):
                raise ToolOperationError("InvalidCommand", "argv must be a non-empty array of strings")
            return raw_argv
        if isinstance(raw_command, str) and raw_command.strip():
            return shlex.split(raw_command)
        raise ToolOperationError("InvalidCommand", "shell execute requires argv or command")

    @staticmethod
    def _validate_allowed_command(executable: str, allowed_commands: list[str]) -> None:
        executable_path = Path(executable)
        is_path_qualified = executable_path.name != executable or executable_path.is_absolute()
        if is_path_qualified:
            if executable not in allowed_commands:
                raise ToolOperationError(
                    "CommandDenied",
                    f"command path is not exactly allowlisted: {executable}",
                    {"command": [executable], "allowedCommands": allowed_commands},
                )
            return
        if executable not in allowed_commands:
            raise ToolOperationError(
                "CommandDenied",
                f"command is not allowlisted: {executable}",
                {"command": [executable], "allowedCommands": allowed_commands},
            )

    def _run_command(
        self,
        invocation: ToolInvocationResource,
        argv: list[str],
        cwd: Path,
        cwd_display: str,
    ) -> Observation:
        timeout_seconds = self._shell_timeout(invocation)
        env = {
            "HOME": str(cwd),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": os.environ.get("PATH", ""),
        }
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            payload = self._command_payload(
                argv,
                cwd_display,
                self._output_text(exc.stdout),
                self._output_text(exc.stderr),
                None,
                True,
            )
            return Observation(
                summary="Command timed out",
                payload=payload,
                error=ObservationError(reason="CommandTimedOut", message="Command timed out"),
            )
        payload = self._command_payload(
            argv,
            cwd_display,
            completed.stdout,
            completed.stderr,
            completed.returncode,
            False,
        )
        if completed.returncode != 0:
            message = f"Command exited with code {completed.returncode}"
            return Observation(
                summary=message,
                payload=payload,
                error=ObservationError(reason="CommandFailed", message=message),
            )
        return Observation(summary="Command completed", payload=payload)

    def _shell_timeout(self, invocation: ToolInvocationResource) -> float | None:
        raw_timeout = invocation.spec.arguments.get("timeoutSeconds")
        if raw_timeout is None:
            return invocation.spec.timeoutSeconds
        if not isinstance(raw_timeout, (int, float)) or isinstance(raw_timeout, bool) or raw_timeout <= 0:
            raise ToolOperationError("InvalidArguments", "timeoutSeconds must be a positive number")
        if invocation.spec.timeoutSeconds is None:
            return float(raw_timeout)
        return min(float(raw_timeout), invocation.spec.timeoutSeconds)

    @staticmethod
    def _command_payload(
        argv: list[str],
        cwd: str,
        stdout: str,
        stderr: str,
        exit_code: int | None,
        timed_out: bool,
    ) -> dict[str, Any]:
        return {
            "command": argv,
            "cwd": cwd,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": exit_code,
            "timedOut": timed_out,
            "sandbox": {"workspaceCwd": cwd, "shell": False},
        }


class ToolRuntimeRegistry:
    def __init__(self, runtimes: dict[str, ToolRuntime] | None = None, *, store: ResourceStore | None = None) -> None:
        self._runtimes: dict[str, ToolRuntime] = {"fake": FakeToolRuntime()}
        if store is not None:
            self._runtimes.update(
                {
                    "filesystem": FilesystemToolRuntime(store),
                    "git": GitToolRuntime(store),
                    "shell": ShellToolRuntime(store),
                }
            )
        if runtimes:
            self._runtimes.update(runtimes)

    def register(self, tool: str, runtime: ToolRuntime) -> None:
        self._runtimes[tool] = runtime

    def resolve(self, invocation: ToolInvocationResource) -> ToolRuntime:
        runtime = self._runtimes.get(invocation.spec.tool)
        if runtime is None:
            raise ToolRuntimeError(f"No ToolRuntime registered for tool {invocation.spec.tool}")
        return runtime

    def execute(self, invocation: ToolInvocationResource) -> Observation:
        return self.resolve(invocation).execute(invocation)


def run_correlation_id(run: AgentRunResource) -> str | None:
    annotated = run.metadata.annotations.get(CORRELATION_ID_ANNOTATION)
    if annotated:
        return annotated
    value = run.status.data.get(CORRELATION_ID_STATUS_KEY)
    return value if isinstance(value, str) else None


class DecisionValidationError(Exception):
    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        self.message = message
        super().__init__(message)


def utciso() -> str:
    return datetime.now(UTC).isoformat()


class AgentRuntime:
    def __init__(
        self,
        store: ResourceStore,
        *,
        model_client_factory: ModelClientFactory = build_model_client,
        tool_runtime_registry: ToolRuntimeRegistry | None = None,
    ) -> None:
        self.store = store
        self.policy_engine = PolicyEngine(store)
        self.model_client_factory = model_client_factory
        self.tool_runtime_registry = tool_runtime_registry or ToolRuntimeRegistry(store=store)

    async def run(self, agent_run: AgentRunResource) -> dict[str, str]:
        namespace = agent_run.metadata.namespace
        if namespace is None:
            raise ValueError("AgentRun must have a namespace")

        for _step in range(1000):
            run = self._refresh_run(agent_run)
            if run.status.phase in AGENT_RUN_TERMINAL_PHASES:
                return {"status": run.status.phase}
            if run.status.phase == "Pending":
                return {"status": run.status.phase}
            if run.status.phase == "WaitingForApproval" and not run.spec.cancellationRequested:
                return {"status": run.status.phase}

            agent = self._load_resource(ResourceKind.AGENT, run.spec.agentRef.name, namespace, AgentResource)
            mission = self._load_resource(ResourceKind.MISSION, run.spec.missionRef.name, namespace, MissionResource)
            workspace = self._load_resource(ResourceKind.WORKSPACE, namespace, None, WorkspaceResource)
            context = self._load_resource(ResourceKind.CONTEXT, run.spec.contextRef.name, namespace, ContextResource)

            cancelled = self._cancel_if_requested(run)
            if cancelled is not None:
                return {"status": cancelled.status.phase}
            timed_out = self._timeout_if_expired(run)
            if timed_out is not None:
                return {"status": timed_out.status.phase}

            phase = run.status.phase
            if phase == "Scheduled":
                run = self._start_engine(run, agent)
                if run.status.phase == "WaitingForApproval":
                    return {"status": run.status.phase}
                continue
            if phase == "Starting":
                run = self._prepare_execution(run, agent, context)
                if run.status.phase in AGENT_RUN_TERMINAL_PHASES:
                    return {"status": run.status.phase}
                continue
            if phase == "AwaitingDecision":
                run = await self._request_decision(run, agent, mission, workspace, context)
                if run.status.phase in {"AwaitingDecision", "WaitingForApproval"}:
                    return {"status": run.status.phase}
                continue
            if phase == "DecisionReady":
                run = self._validate_current_decision(run, agent)
                continue
            if phase == "ProcessingDecision":
                run = self._process_current_decision(run, agent, mission)
                continue
            if phase == "WaitingForTool":
                run = await self._resume_waiting_for_tool(run, agent)
                if run.status.phase in {"WaitingForTool", "WaitingForApproval"}:
                    return {"status": run.status.phase}
                continue
            if phase == "WaitingForObservation":
                run = self._deliver_observation(run)
                continue
            if phase == "Finalizing":
                run = self._finalize_run(run, agent, mission, workspace)
                if run.status.phase == "Succeeded":
                    artifact_path = run.status.data.get("artifactPath")
                    return {"artifactPath": str(artifact_path)} if artifact_path else {"status": "Succeeded"}
                return {"status": run.status.phase}
            return {"status": run.status.phase}

        raise RuntimeError(f"Execution Engine exceeded local step guard for AgentRun {agent_run.metadata.name}")

    def _start_engine(self, run: AgentRunResource, agent: AgentResource) -> AgentRunResource:
        data = dict(run.status.data)
        data.setdefault("executionStartedAt", utciso())
        data["executionState"] = "Starting"
        data["executionOwner"] = EXECUTION_OWNER
        data["executionEpoch"] = int(data.get("executionEpoch", 0)) + 1
        data.setdefault("executionFrames", [])
        data.setdefault("budgetUsage", self._initial_budget_usage())
        data["executionBudget"] = self._budget_snapshot(run.spec.execution)
        started = self._transition(
            run,
            "Starting",
            "Execution Engine started",
            data,
            "ExecutionEngineStarted",
            "StartExecutionEngine",
            "ExecutionEngineStarted",
        )
        self._emit_run_event(
            started,
            "AgentRunStarted",
            "AgentRun execution started",
            {"agent": agent.metadata.name, "budget": data["executionBudget"]},
            "StartAgentRun",
            "AgentRunStarted",
        )
        try:
            self._authorize_declared_tools(agent, started)
        except ApprovalRequired:
            return self._refresh_run(started)
        except PolicyDenied as exc:
            return self._fail_run(started, exc.reason, "PolicyDenied", retryable=False)
        return started

    def _prepare_execution(
        self,
        run: AgentRunResource,
        agent: AgentResource,
        context: ContextResource,
    ) -> AgentRunResource:
        if context.status.phase != "Ready":
            return self._fail_run(
                run,
                f"Context {context.metadata.name} is {context.status.phase}, not Ready",
                "ContextNotReady",
                retryable=True,
            )
        rendered_context = context.status.data.get("renderedContext")
        if not isinstance(rendered_context, str):
            return self._fail_run(
                run,
                f"Context {context.metadata.name} does not contain renderedContext",
                "ContextNotReady",
                retryable=True,
            )
        if not run.status.data.get("contextConsumed"):
            self._emit_context_consumed(run, context, agent)
        data = dict(run.status.data)
        data["executionState"] = "AwaitingDecision"
        data["context"] = context.metadata.name
        data["contextRevision"] = context.metadata.generation
        data["agent"] = agent.metadata.name
        data["contextConsumed"] = True
        return self._transition(
            run,
            "AwaitingDecision",
            "Execution frame prepared",
            data,
            "ExecutionFramePrepared",
            "PrepareExecutionFrame",
            "ExecutionFramePrepared",
        )

    async def _request_decision(
        self,
        run: AgentRunResource,
        agent: AgentResource,
        mission: MissionResource,
        workspace: WorkspaceResource,
        context: ContextResource,
    ) -> AgentRunResource:
        frame_result = self._ensure_active_frame(run, agent, mission, context)
        if frame_result.status.phase in AGENT_RUN_TERMINAL_PHASES:
            return frame_result
        run = frame_result
        data = dict(run.status.data)
        usage = self._budget_usage(data)
        budget = run.spec.execution
        if usage["modelInvocations"] >= budget.maxModelInvocations:
            return self._budget_exceeded(run, "maxModelInvocations")

        model_config = self._model_for_agent(agent, mission, workspace)
        try:
            self._authorize(
                agent,
                run,
                "model",
                "invoke",
                {"provider": model_config.provider, "model": model_config.model},
            )
        except ApprovalRequired:
            return self._refresh_run(run)
        except PolicyDenied as exc:
            return self._fail_run(run, exc.reason, "PolicyDenied", retryable=False)

        frame, frames = self._active_frame(data)
        if frame.get("state") == "decision-requested" and not frame.get("rawDecision"):
            active_invocation = data.get("activeModelInvocation")
            if isinstance(active_invocation, dict):
                if self._model_invocation_expired(active_invocation):
                    return self._timed_out(
                        run,
                        "ModelInvocationTimedOut",
                        "Model invocation timed out before completion",
                    )
                self._emit_run_event(
                    run,
                    "DuplicateModelInvocationPrevented",
                    f"Duplicate model invocation prevented for iteration {frame.get('iteration')}",
                    {
                        "iteration": frame.get("iteration"),
                        "attempt": active_invocation.get("attempt"),
                        "executionEpoch": data.get("executionEpoch"),
                        "reason": "ModelInvocationAlreadyInFlight",
                    },
                    "RequestDecision",
                    "ModelInvocationAlreadyInFlight",
                )
                return run
        attempt = int(frame.get("modelAttempts", 0)) + 1
        invocation_id = self._model_invocation_id(run, frame, attempt)
        deadline = self._model_invocation_deadline(model_config.timeoutSeconds)
        frame["modelAttempts"] = attempt
        frame["decisionRequestedAt"] = utciso()
        frame["state"] = "decision-requested"
        frame["modelInvocation"] = {
            "id": invocation_id,
            "provider": model_config.provider,
            "model": model_config.model,
            "attempt": attempt,
            "state": "running",
            "executionOwner": data.get("executionOwner"),
            "executionEpoch": data.get("executionEpoch"),
            "deadlineAt": deadline,
        }
        usage["modelInvocations"] += 1
        usage["wallTimeSeconds"] = self._wall_time_seconds(data)
        frame["budgetUsage"] = dict(usage)
        data["budgetUsage"] = usage
        data["executionFrames"] = frames
        data["activeFrameIndex"] = frames.index(frame)
        data["activeModelInvocation"] = {
            "id": invocation_id,
            "frameIndex": data["activeFrameIndex"],
            "iteration": frame["iteration"],
            "attempt": attempt,
            "executionOwner": data.get("executionOwner"),
            "executionEpoch": data.get("executionEpoch"),
            "deadlineAt": deadline,
        }
        saved = self._save_run_data(run, data)
        if saved is None:
            return self._refresh_run(run)
        run = saved
        self._emit_budget_updated(run, usage, budget)

        self._emit_run_event(
            run,
            "DecisionRequested",
            f"Decision requested for iteration {frame['iteration']}",
            {
                "iteration": frame["iteration"],
                "attempt": attempt,
                "model": model_config.model,
                "provider": model_config.provider,
                "budget": dict(usage),
            },
            "RequestDecision",
            "DecisionRequested",
        )
        self._emit_run_event(
            run,
            "ModelInvoked",
            f"Invoking {model_config.provider}:{model_config.model}",
            {"provider": model_config.provider, "model": model_config.model, "iteration": frame["iteration"]},
            "InvokeModel",
            "ModelInvoked",
        )

        messages = self._build_messages(mission, agent, context, data, frame, budget)
        client = self.model_client_factory(model_config, self.store)
        try:
            raw_decision = await asyncio.wait_for(client.generate(messages), timeout=model_config.timeoutSeconds)
        except asyncio.TimeoutError:
            fenced = self._fence_model_completion(
                run,
                expected_invocation_id=invocation_id,
                expected_attempt=attempt,
                late_event_reason="ModelInvocationTimedOutAfterTerminal",
            )
            if fenced is None:
                return self._refresh_run(run)
            run = fenced
            data = dict(run.status.data)
            frame, frames = self._active_frame(data)
            frame["modelError"] = {
                "reason": "ModelInvocationTimedOut",
                "message": f"Model invocation timed out after {model_config.timeoutSeconds:g} seconds",
                "attempt": attempt,
            }
            if isinstance(frame.get("modelInvocation"), dict):
                frame["modelInvocation"]["state"] = "timed-out"
            data["activeModelInvocation"] = None
            data["executionFrames"] = frames
            saved = self._save_run_data(run, data)
            if saved is None:
                return self._refresh_run(run)
            return self._timed_out(run, "ModelInvocationTimedOut", frame["modelError"]["message"])
        except Exception as exc:
            fenced = self._fence_model_completion(
                run,
                expected_invocation_id=invocation_id,
                expected_attempt=attempt,
                late_event_reason="ModelInvocationFailedAfterTerminal",
            )
            if fenced is None:
                return self._refresh_run(run)
            run = fenced
            data = dict(run.status.data)
            frame, frames = self._active_frame(data)
            frame["modelError"] = {"reason": "ModelInvocationFailed", "message": str(exc), "attempt": attempt}
            if isinstance(frame.get("modelInvocation"), dict):
                frame["modelInvocation"]["state"] = "failed"
            frame["retryCount"] = int(frame.get("retryCount", 0)) + 1
            frame["modelRetryCount"] = int(frame.get("modelRetryCount", 0)) + 1
            data["executionFrames"] = frames
            data["activeModelInvocation"] = None
            saved = self._save_run_data(run, data)
            if saved is None:
                return self._refresh_run(run)
            if frame["modelRetryCount"] <= budget.maxModelRetries:
                self._emit_retry_scheduled(run, frame, "ModelInvocationFailed", str(exc))
                return self._refresh_run(run)
            return self._fail_run(run, str(exc), "ModelInvocationFailed", retryable=True)

        fenced = self._fence_model_completion(
            run,
            expected_invocation_id=invocation_id,
            expected_attempt=attempt,
            late_event_reason="AgentRunTerminal",
        )
        if fenced is None:
            return self._refresh_run(run)
        run = fenced
        data = dict(run.status.data)
        frame, frames = self._active_frame(data)
        frame["rawDecision"] = raw_decision
        frame["decisionProducedAt"] = utciso()
        frame["state"] = "decision-produced"
        frame["modelInvocation"] = {
            "id": invocation_id,
            "provider": model_config.provider,
            "model": model_config.model,
            "attempt": attempt,
            "state": "completed",
            "executionOwner": data.get("executionOwner"),
            "executionEpoch": data.get("executionEpoch"),
        }
        data["executionFrames"] = frames
        data["activeDecisionSummary"] = {"raw": self._redact_text(raw_decision)}
        return self._transition(
            run,
            "DecisionReady",
            "Decision produced",
            data,
            "DecisionProduced",
            "ProduceDecision",
            "DecisionProduced",
            clear_data_keys=["activeModelInvocation"],
        )

    def _validate_current_decision(self, run: AgentRunResource, agent: AgentResource) -> AgentRunResource:
        data = dict(run.status.data)
        usage = self._budget_usage(data)
        frame, frames = self._active_frame(data)
        raw_decision = frame.get("rawDecision")
        try:
            decision = self._parse_and_validate_decision(raw_decision, agent, run, usage)
        except DecisionValidationError as exc:
            usage["decisionFailures"] += 1
            usage["failures"] += 1
            frame["state"] = "decision-rejected"
            frame["decisionValidation"] = {
                "status": "Rejected",
                "reason": exc.reason,
                "message": exc.message,
                "validatedAt": utciso(),
            }
            frame.setdefault("rejections", []).append(frame["decisionValidation"])
            frame["retryCount"] = int(frame.get("retryCount", 0)) + 1
            frame["invalidDecisionRetryCount"] = int(frame.get("invalidDecisionRetryCount", 0)) + 1
            frame["budgetUsage"] = dict(usage)
            data["budgetUsage"] = usage
            data["executionFrames"] = frames
            data["activeDecisionSummary"] = {"rejected": exc.reason, "message": exc.message}
            self._emit_run_event(
                run,
                "DecisionRejected",
                exc.message,
                {
                    "iteration": frame["iteration"],
                    "attempt": frame.get("modelAttempts", 1),
                    "reason": exc.reason,
                    "budget": dict(usage),
                },
                "RejectDecision",
                exc.reason,
            )
            if usage["decisionFailures"] <= run.spec.execution.maxDecisionFailures:
                rejected = self._transition(
                    run,
                    "AwaitingDecision",
                    exc.message,
                    data,
                    "DecisionRejected",
                    "RejectDecision",
                    exc.reason,
                )
                self._emit_retry_scheduled(rejected, frame, exc.reason, exc.message)
                return rejected
            data["terminalReason"] = exc.reason
            data["retryable"] = False
            data["diagnosticSummary"] = exc.message
            return self._transition(
                run,
                "Failed",
                exc.message,
                data,
                "ExecutionFailed",
                "FailAgentRun",
                exc.reason,
            )

        frame["decision"] = decision
        frame["decisionValidation"] = {
            "status": "Accepted",
            "reason": "DecisionValidated",
            "validatedAt": utciso(),
        }
        frame["state"] = "decision-validated"
        frame["budgetUsage"] = dict(usage)
        data["executionFrames"] = frames
        data["budgetUsage"] = usage
        data["activeDecisionSummary"] = self._decision_summary(decision)
        return self._transition(
            run,
            "ProcessingDecision",
            f"Decision {decision['type']} validated",
            data,
            "DecisionValidated",
            "ValidateDecision",
            "DecisionValidated",
        )

    def _process_current_decision(
        self,
        run: AgentRunResource,
        agent: AgentResource,
        mission: MissionResource,
    ) -> AgentRunResource:
        cancelled = self._cancel_if_requested(run)
        if cancelled is not None:
            return cancelled
        data = dict(run.status.data)
        frame, frames = self._active_frame(data)
        decision = frame.get("decision")
        if not isinstance(decision, dict):
            return self._fail_run(run, "No persisted Decision to process", "DecisionValidationFailed", retryable=False)
        decision_type = decision.get("type")
        if decision_type == "invoke_tool":
            if frame.get("toolInvocation"):
                return self._transition(
                    run,
                    "WaitingForTool",
                    f"Waiting for ToolInvocation {frame['toolInvocation']}",
                    data,
                    "ToolInvocationCreated",
                    "WaitForTool",
                    "ToolInvocationCreated",
                )
            return self._create_tool_invocation(run, agent, decision, frame, frames, data)
        if decision_type == "complete":
            frame["state"] = "finalizing"
            frame["finalizingAt"] = utciso()
            data["completionDecision"] = decision
            data["executionFrames"] = frames
            return self._transition(
                run,
                "Finalizing",
                "Execution finalizing",
                data,
                "ExecutionFinalizing",
                "FinalizeExecution",
                "ExecutionFinalizing",
            )
        if decision_type == "fail":
            reason = str(decision.get("reason") or "AgentRun failed by Decision")
            retryable = bool(decision.get("retryable"))
            return self._fail_run(run, reason, "DecisionFailed", retryable=retryable)
        return self._fail_run(
            run, f"Unsupported Decision type {decision_type}", "DecisionTypeUnsupported", retryable=False
        )

    def _create_tool_invocation(
        self,
        run: AgentRunResource,
        agent: AgentResource,
        decision: dict[str, Any],
        frame: dict[str, Any],
        frames: list[dict[str, Any]],
        data: dict[str, Any],
    ) -> AgentRunResource:
        cancelled = self._cancel_if_requested(run)
        if cancelled is not None:
            return cancelled
        usage = self._budget_usage(data)
        if usage["toolInvocations"] >= run.spec.execution.maxToolInvocations:
            return self._budget_exceeded(run, "maxToolInvocations")

        namespace = run.metadata.namespace
        if namespace is None:
            raise ValueError("AgentRun must have a namespace")
        tool_name = str(decision["tool"])
        operation_name = str(decision["operation"])
        tool, operation = self._tool_and_operation(tool_name, operation_name)
        invocation_name = self._tool_invocation_name(run, frame)
        existing = self.store.get(ResourceKind.TOOL_INVOCATION, invocation_name, namespace)
        if existing is None:
            timeout = operation.timeoutSeconds or tool.spec.timeoutSeconds
            manifest = {
                "apiVersion": "ai.platform/v1",
                "kind": "ToolInvocation",
                "metadata": {
                    "name": invocation_name,
                    "namespace": namespace,
                    "annotations": {
                        EXECUTION_EPOCH_ANNOTATION: str(data.get("executionEpoch", "")),
                        EXECUTION_FRAME_INDEX_ANNOTATION: str(data.get("activeFrameIndex", "")),
                    },
                    "ownerReferences": [{"kind": "AgentRun", "name": run.metadata.name, "controller": True}],
                },
                "spec": {
                    "agentRunRef": {"name": run.metadata.name},
                    "tool": tool_name,
                    "operation": operation_name,
                    "arguments": decision["arguments"],
                    "correlationId": run_correlation_id(run),
                    "idempotencyKey": self._idempotency_key(run, frame, decision),
                    "timeoutSeconds": timeout,
                    "riskLevel": operation.riskLevel or tool.spec.riskLevel,
                },
            }
            self.store.apply(
                manifest,
                event_context=self._context(run, "CreateToolInvocation", "ToolInvocationCreated"),
                field_manager=CONTROLLER_FIELD_MANAGER,
            )
            usage["toolInvocations"] += 1
            self._emit_budget_updated(run, usage, run.spec.execution)

        frame["toolInvocation"] = invocation_name
        frame["state"] = "waiting-for-tool"
        frame["toolInvocationCreatedAt"] = frame.get("toolInvocationCreatedAt") or utciso()
        frame["budgetUsage"] = dict(usage)
        data["budgetUsage"] = usage
        data["executionFrames"] = frames
        data["activeToolInvocation"] = invocation_name
        data["activeDecisionSummary"] = self._decision_summary(decision)
        data["executionState"] = "WaitingForTool"
        data.setdefault("completedToolInvocations", [])
        data.setdefault("unresolvedToolInvocations", [])
        data["agent"] = agent.metadata.name
        return self._transition(
            run,
            "WaitingForTool",
            f"ToolInvocation {invocation_name} created",
            data,
            "ToolInvocationCreated",
            "CreateToolInvocation",
            "ToolInvocationCreated",
        )

    async def _resume_waiting_for_tool(self, run: AgentRunResource, agent: AgentResource) -> AgentRunResource:
        current = self._refresh_run(run)
        if current.status.phase in AGENT_RUN_TERMINAL_PHASES:
            return current
        run = current
        data = dict(run.status.data)
        frame, frames = self._active_frame(data)
        invocation_name = frame.get("toolInvocation") or data.get("activeToolInvocation")
        if not isinstance(invocation_name, str):
            return self._fail_run(run, "No active ToolInvocation recorded", "ExecutionStateInvalid", retryable=False)
        invocation = self._tool_invocation(invocation_name, run.metadata.namespace)
        if invocation.status.phase == "Running":
            observation = self._error_observation(
                "ExecutionStateUnknown",
                "ToolInvocation was already Running; refusing to replay execution",
            )
            self._set_tool_status(
                invocation,
                run,
                "Failed",
                observation.summary,
                "ToolInvocationFailed",
                {"error": observation.summary},
                observation=observation,
            )
            self._record_observation(invocation, run, observation)
            invocation = self._tool_invocation(invocation_name, run.metadata.namespace)
        if invocation.status.phase == "WaitingForApproval" and self._approval_is_pending(invocation):
            return run
        if invocation.status.phase not in TOOL_TERMINAL_PHASES:
            fenced = self._fence_execution_mutation(
                run,
                "StartToolInvocation",
                "ToolInvocationStartFenced",
                expected_phase="WaitingForTool",
                expected_state="WaitingForTool",
            )
            if fenced is None:
                return self._refresh_run(run)
            run = fenced
            status = await self._execute_tool_invocation(run, agent, invocation, frame, frames, data)
            if status == "waiting":
                return self._refresh_run(run)
            run = self._refresh_run(run)
            if run.status.phase in AGENT_RUN_TERMINAL_PHASES:
                return run
            invocation = self._tool_invocation(invocation_name, run.metadata.namespace)
        if invocation.status.phase in TOOL_TERMINAL_PHASES:
            frame["state"] = "tool-observed"
            frame["toolInvocationPhase"] = invocation.status.phase
            frame["toolObservedAt"] = utciso()
            data["executionFrames"] = frames
            data["activeToolInvocation"] = invocation.metadata.name
            return self._transition(
                run,
                "WaitingForObservation",
                f"ToolInvocation {invocation.metadata.name} observed",
                data,
                "ToolInvocationObserved",
                "ObserveToolInvocation",
                "ToolInvocationObserved",
            )
        return self._refresh_run(run)

    async def _execute_tool_invocation(
        self,
        run: AgentRunResource,
        agent: AgentResource,
        invocation: ToolInvocationResource,
        frame: dict[str, Any],
        frames: list[dict[str, Any]],
        data: dict[str, Any],
    ) -> str:
        usage = self._budget_usage(data)
        while True:
            try:
                tool, operation = self._tool_and_operation(invocation.spec.tool, invocation.spec.operation)
                self._validate_arguments(
                    invocation.spec.arguments, operation, f"ToolInvocation {invocation.metadata.name} arguments"
                )
                decision = self.policy_engine.authorize(
                    self._runtime_action(invocation, run, agent),
                    pause_agent_run=False,
                    approval_agent_run=True,
                )
            except ApprovalRequired as exc:
                self._set_tool_status(
                    invocation,
                    run,
                    "WaitingForApproval",
                    f"ToolInvocation waiting for approval {exc.approval_id}",
                    "ToolInvocationWaitingForApproval",
                    {"approval": exc.approval_id, "approvalId": exc.approval_id, "policyDecision": "RequireApproval"},
                )
                self._transition(
                    run,
                    "WaitingForTool",
                    f"Waiting for approval {exc.approval_id}",
                    {
                        **data,
                        "pendingApproval": exc.approval_id,
                        "approval": exc.approval_id,
                        "approvalId": exc.approval_id,
                    },
                    "ExecutionFramePrepared",
                    "WaitForApproval",
                    "ApprovalRequired",
                )
                return "waiting"
            except PolicyDenied as exc:
                observation = self._error_observation("PolicyDenied", exc.reason)
                self._set_tool_status(
                    invocation,
                    run,
                    "Denied",
                    "ToolInvocation denied by Policy",
                    "ToolInvocationDenied",
                    {"policyDecision": "Deny", "reason": exc.reason},
                    observation=observation,
                )
                self._record_observation(invocation, run, observation)
                return "terminal"
            except Exception as exc:
                observation = self._error_observation("ToolInvocationFailed", str(exc))
                self._set_tool_status(
                    invocation,
                    run,
                    "Failed",
                    str(exc),
                    "ToolInvocationFailed",
                    {"error": str(exc)},
                    observation=observation,
                )
                self._record_observation(invocation, run, observation)
                return "terminal"

            runtime = self.tool_runtime_registry.resolve(invocation)
            runtime_id = runtime.runtime_id
            self._set_tool_status(
                invocation,
                run,
                "Authorized",
                "ToolInvocation authorized by Policy",
                "ToolInvocationAuthorized",
                {
                    "runtime": runtime_id,
                    "policyDecision": decision.effect.value,
                    "policy": decision.policy_name,
                    "ruleIndex": decision.rule_index,
                },
            )
            self._set_tool_status(
                invocation,
                run,
                "Running",
                "ToolInvocation execution started",
                "ToolInvocationStarted",
                {"runtime": runtime_id},
            )
            try:
                observation = await self._execute_runtime(runtime, invocation, tool, operation)
            except TimeoutError:
                timeout_seconds = self._effective_timeout(invocation, tool, operation)
                timeout_display = f"{timeout_seconds:g}" if timeout_seconds is not None else "unknown"
                message = f"ToolInvocation timed out after {timeout_display} seconds"
                usage["toolFailures"] += 1
                usage["failures"] += 1
                frame["toolRetryCount"] = int(frame.get("toolRetryCount", 0)) + 1
                frame["budgetUsage"] = dict(usage)
                data["budgetUsage"] = usage
                data["executionFrames"] = frames
                saved = self._save_run_data(run, data)
                if saved is None:
                    return "terminal"
                observation = self._error_observation("ToolInvocationTimedOut", message)
                self._set_tool_status(
                    invocation,
                    run,
                    "TimedOut",
                    message,
                    "ToolInvocationTimedOut",
                    {"runtime": runtime_id, "error": message, "timeoutSeconds": timeout_seconds},
                    observation=observation,
                )
                self._record_observation(invocation, run, observation)
                return "terminal"
            except Exception as exc:
                usage["toolFailures"] += 1
                usage["failures"] += 1
                frame["toolRetryCount"] = int(frame.get("toolRetryCount", 0)) + 1
                frame["retryCount"] = int(frame.get("retryCount", 0)) + 1
                frame["budgetUsage"] = dict(usage)
                data["budgetUsage"] = usage
                data["executionFrames"] = frames
                saved = self._save_run_data(run, data)
                if saved is None:
                    return "terminal"
                run = saved
                if usage["toolFailures"] > run.spec.execution.maxToolFailures:
                    observation = self._error_observation("ToolFailureBudgetExceeded", str(exc))
                    self._set_tool_status(
                        invocation,
                        run,
                        "Failed",
                        str(exc),
                        "ToolInvocationFailed",
                        {"runtime": runtime_id, "error": str(exc)},
                        observation=observation,
                    )
                    self._record_observation(invocation, run, observation)
                    run = self._budget_exceeded(run, "maxToolFailures")
                    return "terminal"
                if frame["toolRetryCount"] <= run.spec.execution.maxToolRetries:
                    self._emit_retry_scheduled(run, frame, "ToolRuntimeError", str(exc))
                    self.store.update_status(
                        ResourceKind.TOOL_INVOCATION,
                        invocation.metadata.name,
                        invocation.metadata.namespace,
                        "Pending",
                        "Tool runtime retry scheduled",
                        {"runtime": runtime_id, "retryCount": frame["toolRetryCount"]},
                        event_context=self._tool_context(
                            invocation, run, "RetryToolInvocation", "ExecutionRetryScheduled"
                        ),
                    )
                    invocation = self._tool_invocation(invocation.metadata.name, invocation.metadata.namespace)
                    continue
                observation = self._error_observation("ToolRuntimeError", str(exc))
                self._set_tool_status(
                    invocation,
                    run,
                    "Failed",
                    str(exc),
                    "ToolInvocationFailed",
                    {"runtime": runtime_id, "error": str(exc)},
                    observation=observation,
                )
                self._record_observation(invocation, run, observation)
                return "terminal"

            self._set_tool_status(
                invocation,
                run,
                "Succeeded",
                "ToolInvocation completed successfully",
                "ToolInvocationCompleted",
                {"runtime": runtime_id},
                observation=observation,
            )
            self._record_observation(invocation, run, observation)
            return "terminal"

    def _deliver_observation(self, run: AgentRunResource) -> AgentRunResource:
        fenced = self._fence_execution_mutation(
            run,
            "DeliverObservation",
            "ObservationDeliveryFenced",
            expected_phase="WaitingForObservation",
            expected_state="WaitingForObservation",
        )
        if fenced is None:
            return self._refresh_run(run)
        run = fenced
        data = dict(run.status.data)
        frame, frames = self._active_frame(data)
        invocation_name = frame.get("toolInvocation") or data.get("activeToolInvocation")
        if not isinstance(invocation_name, str):
            return self._fail_run(run, "No ToolInvocation to deliver", "ExecutionStateInvalid", retryable=False)
        invocation = self._tool_invocation(invocation_name, run.metadata.namespace)
        observation = invocation.status.observation
        if observation is None:
            return self._fail_run(
                run,
                f"ToolInvocation {invocation.metadata.name} has no Observation",
                "ObservationMissing",
                retryable=True,
            )
        observation_payload = observation.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        frame["observation"] = observation_payload
        frame["observationDeliveredAt"] = utciso()
        frame["state"] = "observation-delivered"
        data["executionFrames"] = frames
        completed = list(data.get("completedToolInvocations") or [])
        unresolved = list(data.get("unresolvedToolInvocations") or [])
        if observation.error is None:
            if invocation.metadata.name not in completed:
                completed.append(invocation.metadata.name)
            data["lastSuccessfulIteration"] = frame["iteration"]
        elif invocation.metadata.name not in unresolved:
            unresolved.append(invocation.metadata.name)
        data["completedToolInvocations"] = completed
        data["unresolvedToolInvocations"] = unresolved
        data["executionState"] = "AwaitingDecision"
        return self._transition(
            run,
            "AwaitingDecision",
            f"Observation delivered for ToolInvocation {invocation.metadata.name}",
            data,
            "ObservationDelivered",
            "DeliverObservation",
            "ObservationDelivered",
            clear_data_keys=[
                "activeDecisionSummary",
                "activeToolInvocation",
                "pendingApproval",
                "approval",
                "approvalId",
                "activeModelInvocation",
            ],
        )

    def _finalize_run(
        self,
        run: AgentRunResource,
        agent: AgentResource,
        mission: MissionResource,
        workspace: WorkspaceResource,
    ) -> AgentRunResource:
        fenced = self._fence_execution_mutation(
            run,
            "FinalizeExecution",
            "FinalizationFenced",
            expected_phase="Finalizing",
            expected_state="Finalizing",
        )
        if fenced is None:
            return self._refresh_run(run)
        run = fenced
        data = dict(run.status.data)
        frame, frames = self._active_frame(data)
        decision = frame.get("decision")
        if not isinstance(decision, dict) or decision.get("type") != "complete":
            return self._fail_run(run, "No completion Decision to finalize", "FinalizationFailed", retryable=True)
        try:
            self._validate_outputs(mission, decision)
            artifact_path = self._write_artifact(workspace, mission, agent, run, str(decision["summary"]))
            artifact = self._record_artifact_resource(run, agent, mission, artifact_path)
        except ApprovalRequired:
            return self._refresh_run(run)
        except Exception as exc:
            return self._fail_run(run, str(exc), "FinalizationFailed", retryable=True)
        frame["state"] = "finalized"
        frame["finalizedAt"] = utciso()
        data["executionFrames"] = frames
        data["artifact"] = artifact["metadata"]["name"]
        data["artifactPath"] = str(artifact_path)
        data["completedOutputs"] = decision.get("outputs", [])
        data["terminalReason"] = "Completed"
        data["retryable"] = False
        data["diagnosticSummary"] = decision["summary"]
        data["executionState"] = "Succeeded"
        succeeded = self._transition(
            run,
            "Succeeded",
            "AgentRun completed successfully",
            data,
            "ExecutionCompleted",
            "CompleteExecution",
            "ExecutionCompleted",
            clear_data_keys=["activeDecisionSummary", "activeToolInvocation", "activeFrameIndex"],
        )
        self._emit_run_event(
            succeeded,
            "AgentRunCompleted",
            "AgentRun completed successfully",
            {"artifact": artifact["metadata"]["name"], "artifactPath": str(artifact_path)},
            "CompleteAgentRun",
            "AgentRunCompleted",
        )
        return succeeded

    def _authorize_declared_tools(self, agent: AgentResource, run: AgentRunResource) -> None:
        for tool_name in agent.spec.tools:
            self._authorize(agent, run, tool_name, "use", {"source": "agent.spec.tools"})

    def _authorize(
        self,
        agent: AgentResource,
        run: AgentRunResource,
        tool: str,
        operation: str,
        details: dict[str, object],
    ) -> None:
        self.policy_engine.authorize(
            RuntimeAction(
                tool=tool,
                operation=operation,
                details=details,
                workspace=run.metadata.namespace,
                mission=run.spec.missionRef.name,
                agent=agent.metadata.name,
                agentRun=run.metadata.name,
                correlation_id=run_correlation_id(run),
            )
        )

    def _context(self, run: AgentRunResource, action: str, reason: str) -> EventContext:
        return EventContext(
            controller="AgentRuntime",
            action=action,
            reason=reason,
            correlation_id=run_correlation_id(run),
            workspace=run.metadata.namespace,
            mission=run.spec.missionRef.name,
        )

    def _load_resource(
        self,
        kind: ResourceKind,
        name: str,
        namespace: str | None,
        expected_type: type[_ResourceT],
    ) -> _ResourceT:
        manifest = self.store.get(kind, name, namespace)
        if manifest is None:
            raise KeyError(f"{kind.value} {name} not found")
        resource = parse_resource(manifest)
        if not isinstance(resource, expected_type):
            raise TypeError(f"expected {expected_type.__name__}, got {type(resource).__name__}")
        return resource

    def _model_for_agent(
        self,
        agent: AgentResource,
        mission: MissionResource,
        workspace: WorkspaceResource,
    ) -> ModelConfig:
        if agent.spec.pilot and agent.spec.pilot.modelRef:
            model = self._load_resource(ResourceKind.MODEL, agent.spec.pilot.modelRef, None, ModelResource)
            return model.spec.config
        return agent.spec.model or mission.spec.model or workspace.spec.model

    def _build_messages(
        self,
        mission: MissionResource,
        agent: AgentResource,
        context: ContextResource,
        data: dict[str, Any],
        frame: dict[str, Any],
        budget: ExecutionSpec,
    ) -> list[Message]:
        rendered_context = context.status.data.get("renderedContext")
        user_parts = []
        if mission.spec.objective:
            user_parts.append(f"Objective: {mission.spec.objective}")
        if mission.spec.template:
            user_parts.append(f"Template: {mission.spec.template}")
        user_parts.append(
            "Agent:\n"
            + json.dumps(
                {
                    "name": agent.metadata.name,
                    "role": agent.spec.role,
                    "capabilities": agent.spec.capabilities,
                    "tools": self._available_tool_contracts(agent),
                },
                sort_keys=True,
            )
        )
        if rendered_context:
            user_parts.append(f"Context:\n{rendered_context}")
        if mission.spec.outputs:
            outputs = ", ".join(name for name, enabled in mission.spec.outputs.items() if enabled)
            if outputs:
                user_parts.append(f"Requested outputs: {outputs}")
        user_parts.append(
            "Current budgets:\n"
            + json.dumps(
                {"usage": self._budget_usage(data), "limits": self._budget_snapshot(budget)},
                sort_keys=True,
            )
        )
        prior_frames = [
            {
                "iteration": item.get("iteration"),
                "decision": item.get("decision"),
                "observation": item.get("observation"),
                "validation": item.get("decisionValidation"),
            }
            for item in data.get("executionFrames", [])
            if isinstance(item, dict) and item.get("iteration") != frame.get("iteration")
        ]
        if prior_frames:
            user_parts.append(f"Prior execution frames:\n{json.dumps(prior_frames, sort_keys=True)}")
        if frame.get("rejections"):
            user_parts.append(f"Validation feedback:\n{json.dumps(frame['rejections'], sort_keys=True)}")
        return [
            {
                "role": "system",
                "content": (
                    "You are an autonomous agent in a declarative AI control plane. "
                    "Return exactly one JSON Decision object using version v1 and type "
                    "invoke_tool, complete, or fail. Use only tools listed in the Agent section. "
                    "For invoke_tool, set tool, operation, and arguments exactly as the tool contract requires. "
                    "Do not return natural-language text outside JSON."
                ),
            },
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]

    def _available_tool_contracts(self, agent: AgentResource) -> list[dict[str, Any]]:
        contracts: list[dict[str, Any]] = []
        for tool_name in agent.spec.tools:
            manifest = self.store.get(ResourceKind.TOOL, tool_name)
            if manifest is None:
                continue
            tool = parse_resource(manifest)
            if not isinstance(tool, ToolResource):
                continue
            contracts.append(
                {
                    "name": tool.metadata.name,
                    "description": tool.spec.description,
                    "operations": [
                        operation.model_dump(mode="json", exclude_none=True) for operation in tool.spec.operations
                    ],
                    "timeoutSeconds": tool.spec.timeoutSeconds,
                    "riskLevel": tool.spec.riskLevel,
                }
            )
        return contracts

    def _emit_context_consumed(
        self,
        run: AgentRunResource,
        context: ContextResource,
        agent: AgentResource,
    ) -> None:
        self.store.emit_event(
            "ContextConsumed",
            ResourceKind.CONTEXT,
            context.metadata.name,
            run.metadata.namespace,
            f"Context {context.metadata.name} consumed by AgentRun {run.metadata.name}",
            {
                "agent": agent.metadata.name,
                "agentRun": run.metadata.name,
                "knowledgeIndex": context.spec.knowledgeIndex,
                "chunkCount": context.status.data.get("chunkCount", 0),
                "sources": context.status.data.get("sources", []),
            },
            event_context=self._context(run, "ConsumeContext", "ContextConsumed"),
        )

    def _write_artifact(
        self,
        workspace: WorkspaceResource,
        mission: MissionResource,
        agent: AgentResource,
        run: AgentRunResource,
        content: str,
    ) -> Path:
        relative_path = self._artifact_relative_path(mission, run)
        workspace_root = workspace.spec.resolved_root(self.store.platform_root, workspace.metadata.name)
        artifact_path = workspace_root / relative_path
        self._authorize(agent, run, "filesystem", "write", {"path": str(artifact_path), "artifact": True})
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(content, encoding="utf-8")
        return artifact_path

    def _record_artifact_resource(
        self,
        run: AgentRunResource,
        agent: AgentResource,
        mission: MissionResource,
        artifact_path: Path,
    ) -> dict:
        namespace = run.metadata.namespace
        if namespace is None:
            raise ValueError("AgentRun must have a namespace")
        artifact_name = f"{run.metadata.name}-artifact"
        relative_path = self._artifact_relative_path(mission, run)
        self.store.apply(
            {
                "apiVersion": "ai.platform/v1",
                "kind": "Artifact",
                "metadata": {
                    "name": artifact_name,
                    "namespace": namespace,
                    "ownerReferences": [{"kind": "AgentRun", "name": run.metadata.name, "controller": True}],
                },
                "spec": {
                    "type": "markdown",
                    "path": str(relative_path),
                    "producedBy": {"kind": "AgentRun", "name": run.metadata.name},
                },
            },
            event_context=self._context(run, "CreateArtifact", "ArtifactCreated"),
            field_manager=CONTROLLER_FIELD_MANAGER,
        )
        return self.store.update_status(
            ResourceKind.ARTIFACT,
            artifact_name,
            namespace,
            "Ready",
            f"Artifact written to {artifact_path}",
            {
                "path": str(artifact_path),
                "absolutePath": str(artifact_path),
                "mission": mission.metadata.name,
                "agent": agent.metadata.name,
                "agentRun": run.metadata.name,
            },
            event_type="ArtifactReady",
            event_context=self._context(run, "CreateArtifact", "ArtifactReady"),
        )

    @staticmethod
    def _artifact_relative_path(mission: MissionResource, run: AgentRunResource) -> Path:
        return Path("artifacts") / mission.metadata.name / f"{run.metadata.name}.md"

    def _refresh_run(self, run: AgentRunResource) -> AgentRunResource:
        return self._load_resource(ResourceKind.AGENT_RUN, run.metadata.name, run.metadata.namespace, AgentRunResource)

    def _transition(
        self,
        run: AgentRunResource,
        phase: str,
        message: str,
        data: dict[str, Any],
        event_type: str,
        action: str,
        reason: str,
        *,
        clear_data_keys: list[str] | None = None,
    ) -> AgentRunResource:
        fenced = self._fence_execution_mutation(
            run,
            action,
            reason,
            target_phase=phase,
            expected_phase=run.status.phase,
        )
        if fenced is None:
            return self._refresh_run(run)
        run = fenced
        data = dict(data)
        data["executionState"] = phase
        usage = self._budget_usage(data)
        usage["wallTimeSeconds"] = self._wall_time_seconds(data)
        data["budgetUsage"] = usage
        manifest = self.store.update_status(
            ResourceKind.AGENT_RUN,
            run.metadata.name,
            run.metadata.namespace,
            phase,
            message,
            data,
            event_type=event_type,
            event_context=self._context(run, action, reason),
            clear_data_keys=clear_data_keys,
        )
        parsed = parse_resource(manifest)
        if not isinstance(parsed, AgentRunResource):
            raise TypeError(f"expected AgentRunResource, got {type(parsed).__name__}")
        return parsed

    def _save_run_data(self, run: AgentRunResource, data: dict[str, Any]) -> AgentRunResource | None:
        expected_state = run.status.data.get("executionState")
        expected_active_invocation = run.status.data.get("activeModelInvocation")
        fenced = self._fence_execution_mutation(
            run,
            "PersistExecutionState",
            "ExecutionStatePersisted",
            target_phase=run.status.phase,
            expected_phase=run.status.phase,
            expected_state=expected_state if isinstance(expected_state, str) else None,
        )
        if fenced is None:
            return None
        run = fenced
        current_active_invocation = fenced.status.data.get("activeModelInvocation")
        if current_active_invocation != expected_active_invocation:
            self._emit_stale_execution_fenced(
                fenced,
                "PersistExecutionState",
                "ActiveModelInvocationMismatch",
                {
                    "activeModelInvocation": current_active_invocation,
                    "expectedActiveModelInvocation": expected_active_invocation,
                    "reason": "ActiveModelInvocationMismatch",
                },
            )
            return None
        manifest = self.store.update_status(
            ResourceKind.AGENT_RUN,
            run.metadata.name,
            run.metadata.namespace,
            run.status.phase,
            run.status.message,
            data,
            event_context=self._context(run, "PersistExecutionState", "ExecutionStatePersisted"),
        )
        parsed = parse_resource(manifest)
        if not isinstance(parsed, AgentRunResource):
            raise TypeError(f"expected AgentRunResource, got {type(parsed).__name__}")
        return parsed

    def _fence_execution_mutation(
        self,
        run: AgentRunResource,
        action: str,
        reason: str,
        *,
        target_phase: str | None = None,
        expected_phase: str | None = None,
        expected_state: str | None = None,
    ) -> AgentRunResource | None:
        current = self._refresh_run(run)
        if current.status.phase in AGENT_RUN_TERMINAL_PHASES:
            if target_phase == current.status.phase:
                return current
            self._emit_stale_execution_fenced(
                current,
                action,
                reason,
                {
                    "currentPhase": current.status.phase,
                    "attemptedPhase": target_phase,
                    "expectedPhase": expected_phase,
                    "reason": "AgentRunTerminal",
                },
            )
            return None
        if expected_phase is not None and current.status.phase != expected_phase:
            self._emit_stale_execution_fenced(
                current,
                action,
                reason,
                {
                    "currentPhase": current.status.phase,
                    "expectedPhase": expected_phase,
                    "attemptedPhase": target_phase,
                    "reason": "PhaseMismatch",
                },
            )
            return None
        current_data = current.status.data
        expected_epoch = run.status.data.get("executionEpoch")
        current_epoch = current_data.get("executionEpoch")
        if expected_epoch is not None and current_epoch != expected_epoch:
            self._emit_stale_execution_fenced(
                current,
                action,
                reason,
                {
                    "executionEpoch": expected_epoch,
                    "currentExecutionEpoch": current_epoch,
                    "reason": "ExecutionEpochMismatch",
                },
            )
            return None
        expected_owner = run.status.data.get("executionOwner")
        current_owner = current_data.get("executionOwner")
        if expected_owner is not None and current_owner != expected_owner:
            self._emit_stale_execution_fenced(
                current,
                action,
                reason,
                {
                    "executionOwner": expected_owner,
                    "currentExecutionOwner": current_owner,
                    "reason": "ExecutionOwnerMismatch",
                },
            )
            return None
        if expected_state is not None and current_data.get("executionState") != expected_state:
            self._emit_stale_execution_fenced(
                current,
                action,
                reason,
                {
                    "executionState": current_data.get("executionState"),
                    "expectedExecutionState": expected_state,
                    "reason": "ExecutionStateMismatch",
                },
            )
            return None
        return current

    def _fence_model_completion(
        self,
        run: AgentRunResource,
        *,
        expected_invocation_id: str,
        expected_attempt: int,
        late_event_reason: str,
    ) -> AgentRunResource | None:
        current = self._refresh_run(run)
        if current.status.phase in AGENT_RUN_TERMINAL_PHASES:
            active = current.status.data.get("activeModelInvocation")
            payload = active if isinstance(active, dict) else {}
            self._emit_run_event(
                current,
                "LateModelResponseDiscarded",
                f"Discarded late model response for AgentRun {current.metadata.name}",
                {
                    "modelInvocation": expected_invocation_id,
                    "attempt": expected_attempt,
                    "executionEpoch": run.status.data.get("executionEpoch"),
                    "currentExecutionEpoch": current.status.data.get("executionEpoch"),
                    "currentTerminalPhase": current.status.phase,
                    "reason": late_event_reason,
                    **payload,
                },
                "DiscardModelResponse",
                late_event_reason,
            )
            return None
        fenced = self._fence_execution_mutation(
            run,
            "CompleteModelInvocation",
            "ModelInvocationCompletionFenced",
            expected_phase="AwaitingDecision",
            expected_state="AwaitingDecision",
        )
        if fenced is None:
            return None
        active = fenced.status.data.get("activeModelInvocation")
        if not isinstance(active, dict) or active.get("id") != expected_invocation_id:
            self._emit_stale_execution_fenced(
                fenced,
                "CompleteModelInvocation",
                "ModelInvocationMismatch",
                {
                    "modelInvocation": expected_invocation_id,
                    "activeModelInvocation": active,
                    "attempt": expected_attempt,
                    "reason": "ModelInvocationMismatch",
                },
            )
            return None
        if int(active.get("attempt") or 0) != expected_attempt:
            self._emit_stale_execution_fenced(
                fenced,
                "CompleteModelInvocation",
                "ModelInvocationAttemptMismatch",
                {
                    "modelInvocation": expected_invocation_id,
                    "attempt": expected_attempt,
                    "activeAttempt": active.get("attempt"),
                    "reason": "ModelInvocationAttemptMismatch",
                },
            )
            return None
        return fenced

    def _emit_stale_execution_fenced(
        self,
        run: AgentRunResource,
        action: str,
        reason: str,
        payload: dict[str, Any],
    ) -> None:
        self._emit_run_event(
            run,
            "StaleExecutionFenced",
            f"Stale execution mutation fenced for AgentRun {run.metadata.name}",
            {
                "executionOwner": run.status.data.get("executionOwner"),
                "executionEpoch": run.status.data.get("executionEpoch"),
                **payload,
            },
            action,
            reason,
        )

    def _emit_run_event(
        self,
        run: AgentRunResource,
        event_type: str,
        message: str,
        payload: dict[str, Any],
        action: str,
        reason: str,
    ) -> None:
        self.store.emit_event(
            event_type,
            ResourceKind.AGENT_RUN,
            run.metadata.name,
            run.metadata.namespace,
            message,
            {
                "agentRun": run.metadata.name,
                "budget": run.status.data.get("budgetUsage"),
                **payload,
            },
            event_context=self._context(run, action, reason),
        )

    def _ensure_active_frame(
        self,
        run: AgentRunResource,
        agent: AgentResource,
        mission: MissionResource,
        context: ContextResource,
    ) -> AgentRunResource:
        data = dict(run.status.data)
        frames = self._frames(data)
        active_index = data.get("activeFrameIndex")
        active = frames[active_index] if isinstance(active_index, int) and 0 <= active_index < len(frames) else None
        if active is not None:
            state = str(active.get("state") or "")
            if state in {"observation-delivered", "finalized", "decision-rejected"}:
                active = None
            elif state == "tool-observed":
                data["executionFrames"] = frames
                return self._transition(
                    run,
                    "WaitingForObservation",
                    "Waiting for Observation delivery",
                    data,
                    "ExecutionFramePrepared",
                    "ResumeObservationFrame",
                    "ExecutionFramePrepared",
                )
        if active is not None:
            state = str(active.get("state") or "")
            if active.get("toolInvocation") and not active.get("observation"):
                data["executionFrames"] = frames
                data["activeToolInvocation"] = active["toolInvocation"]
                return self._transition(
                    run,
                    "WaitingForTool",
                    f"Waiting for ToolInvocation {active['toolInvocation']}",
                    data,
                    "ExecutionFramePrepared",
                    "ResumeToolFrame",
                    "ExecutionFramePrepared",
                )
            if active.get("decision") and state not in {"observation-delivered", "finalized", "decision-rejected"}:
                data["executionFrames"] = frames
                return self._transition(
                    run,
                    "ProcessingDecision",
                    "Resuming persisted Decision",
                    data,
                    "ExecutionFramePrepared",
                    "ResumeDecisionFrame",
                    "ExecutionFramePrepared",
                )
            if active.get("rawDecision") and state not in {"observation-delivered", "finalized", "decision-rejected"}:
                data["executionFrames"] = frames
                return self._transition(
                    run,
                    "DecisionReady",
                    "Resuming persisted Decision response",
                    data,
                    "ExecutionFramePrepared",
                    "ResumeDecisionFrame",
                    "ExecutionFramePrepared",
                )
            if state == "decision-requested" and not active.get("rawDecision"):
                return run
            if state not in {"observation-delivered", "finalized", "decision-rejected"}:
                return run

        usage = self._budget_usage(data)
        if usage["iterations"] >= run.spec.execution.maxIterations:
            return self._budget_exceeded(run, "maxIterations")
        usage["iterations"] += 1
        usage["wallTimeSeconds"] = self._wall_time_seconds(data)
        iteration = usage["iterations"]
        frame = {
            "iteration": iteration,
            "state": "prepared",
            "preparedAt": utciso(),
            "retryCount": 0,
            "modelRetryCount": 0,
            "invalidDecisionRetryCount": 0,
            "toolRetryCount": 0,
            "budgetUsage": dict(usage),
            "agent": agent.metadata.name,
            "mission": mission.metadata.name,
            "context": context.metadata.name,
            "contextRevision": context.metadata.generation,
            "correlationId": run_correlation_id(run),
            "pilot": (agent.spec.pilot.model_dump(mode="json", exclude_none=True) if agent.spec.pilot else None),
        }
        frames.append(frame)
        data["executionFrames"] = frames
        data["activeFrameIndex"] = len(frames) - 1
        data["budgetUsage"] = usage
        return self._transition(
            run,
            "AwaitingDecision",
            f"Execution frame {iteration} prepared",
            data,
            "ExecutionFramePrepared",
            "PrepareExecutionFrame",
            "ExecutionFramePrepared",
        )

    @staticmethod
    def _frames(data: dict[str, Any]) -> list[dict[str, Any]]:
        frames = data.get("executionFrames")
        return [dict(item) for item in frames] if isinstance(frames, list) else []

    def _active_frame(self, data: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        frames = self._frames(data)
        active_index = data.get("activeFrameIndex")
        if not isinstance(active_index, int) or not 0 <= active_index < len(frames):
            raise RuntimeError("AgentRun has no active ExecutionFrame")
        return frames[active_index], frames

    @staticmethod
    def _initial_budget_usage() -> dict[str, Any]:
        return {
            "iterations": 0,
            "modelInvocations": 0,
            "toolInvocations": 0,
            "decisionFailures": 0,
            "toolFailures": 0,
            "failures": 0,
            "wallTimeSeconds": 0.0,
            "inputTokens": "Unknown",
            "outputTokens": "Unknown",
        }

    def _budget_usage(self, data: dict[str, Any]) -> dict[str, Any]:
        usage = self._initial_budget_usage()
        raw = data.get("budgetUsage")
        if isinstance(raw, dict):
            usage.update(raw)
        for key in (
            "iterations",
            "modelInvocations",
            "toolInvocations",
            "decisionFailures",
            "toolFailures",
            "failures",
        ):
            usage[key] = int(usage.get(key) or 0)
        usage["wallTimeSeconds"] = float(usage.get("wallTimeSeconds") or 0.0)
        return usage

    @staticmethod
    def _budget_snapshot(budget: ExecutionSpec) -> dict[str, Any]:
        return budget.model_dump(mode="json")

    def _wall_time_seconds(self, data: dict[str, Any]) -> float:
        started_at = data.get("executionStartedAt")
        if not isinstance(started_at, str):
            return 0.0
        try:
            started = datetime.fromisoformat(started_at)
        except ValueError:
            return 0.0
        return max(0.0, (datetime.now(UTC) - started).total_seconds())

    def _emit_budget_updated(self, run: AgentRunResource, usage: dict[str, Any], budget: ExecutionSpec) -> None:
        self._emit_run_event(
            run,
            "ExecutionBudgetUpdated",
            "Execution budget usage updated",
            {"usage": dict(usage), "limits": self._budget_snapshot(budget)},
            "UpdateBudget",
            "ExecutionBudgetUpdated",
        )

    def _model_invocation_id(self, run: AgentRunResource, frame: dict[str, Any], attempt: int) -> str:
        return (
            f"{run.metadata.name}-model-"
            f"{run.status.data.get('executionEpoch', 0)}-"
            f"{frame.get('iteration', 0)}-{attempt}"
        )

    @staticmethod
    def _model_invocation_deadline(timeout_seconds: float) -> str:
        deadline = datetime.now(UTC).timestamp() + timeout_seconds
        return datetime.fromtimestamp(deadline, UTC).isoformat()

    @staticmethod
    def _model_invocation_expired(active_invocation: dict[str, Any]) -> bool:
        deadline = active_invocation.get("deadlineAt")
        if not isinstance(deadline, str):
            return False
        try:
            return datetime.now(UTC) >= datetime.fromisoformat(deadline)
        except ValueError:
            return False

    def _cancel_if_requested(self, run: AgentRunResource) -> AgentRunResource | None:
        if not run.spec.cancellationRequested:
            return None
        data = dict(run.status.data)
        if not data.get("cancellationRequestedEvent"):
            data["cancellationRequestedEvent"] = utciso()
            self._emit_run_event(
                run,
                "CancellationRequested",
                "AgentRun cancellation requested",
                {"agentRun": run.metadata.name},
                "RequestCancellation",
                "CancellationRequested",
            )
        self._emit_run_event(
            run,
            "CancellationAcknowledged",
            "AgentRun cancellation acknowledged",
            {"agentRun": run.metadata.name},
            "AcknowledgeCancellation",
            "CancellationAcknowledged",
        )
        data["terminalReason"] = "CancellationRequested"
        data["retryable"] = False
        data["diagnosticSummary"] = "AgentRun cancelled"
        return self._transition(
            run,
            "Cancelled",
            "AgentRun cancelled",
            data,
            "ExecutionCancelled",
            "CancelExecution",
            "ExecutionCancelled",
            clear_data_keys=[
                "activeDecisionSummary",
                "activeToolInvocation",
                "pendingApproval",
                "approval",
                "approvalId",
                "activeModelInvocation",
            ],
        )

    def _timeout_if_expired(self, run: AgentRunResource) -> AgentRunResource | None:
        data = dict(run.status.data)
        if "executionStartedAt" not in data:
            return None
        wall_time = self._wall_time_seconds(data)
        if wall_time < run.spec.execution.maxWallTimeSeconds:
            return None
        return self._timed_out(
            run,
            "AgentRunTimedOut",
            f"AgentRun timed out after {run.spec.execution.maxWallTimeSeconds:g} seconds",
        )

    def _timed_out(self, run: AgentRunResource, reason: str, message: str) -> AgentRunResource:
        data = dict(run.status.data)
        data.pop("activeModelInvocation", None)
        data["terminalReason"] = reason
        data["retryable"] = False
        data["diagnosticSummary"] = message
        return self._transition(
            run,
            "TimedOut",
            message,
            data,
            "ExecutionTimedOut",
            "TimeoutExecution",
            reason,
            clear_data_keys=[
                "activeDecisionSummary",
                "activeToolInvocation",
                "pendingApproval",
                "approval",
                "approvalId",
                "activeModelInvocation",
            ],
        )

    def _budget_exceeded(self, run: AgentRunResource, limit: str) -> AgentRunResource:
        data = dict(run.status.data)
        usage = self._budget_usage(data)
        usage["wallTimeSeconds"] = self._wall_time_seconds(data)
        data["budgetUsage"] = usage
        data["exceededBudget"] = limit
        data["terminalReason"] = "BudgetExceeded"
        data["retryable"] = False
        data["diagnosticSummary"] = f"Execution budget exceeded: {limit}"
        return self._transition(
            run,
            "BudgetExceeded",
            f"Execution budget exceeded: {limit}",
            data,
            "ExecutionBudgetExceeded",
            "ExceedBudget",
            limit,
            clear_data_keys=[
                "activeDecisionSummary",
                "activeToolInvocation",
                "pendingApproval",
                "approval",
                "approvalId",
                "activeModelInvocation",
            ],
        )

    def _fail_run(self, run: AgentRunResource, message: str, reason: str, *, retryable: bool) -> AgentRunResource:
        data = dict(run.status.data)
        data["terminalReason"] = reason
        data["retryable"] = retryable
        data["diagnosticSummary"] = message
        failed = self._transition(
            run,
            "Failed",
            message,
            data,
            "ExecutionFailed",
            "FailExecution",
            reason,
            clear_data_keys=[
                "activeDecisionSummary",
                "activeToolInvocation",
                "pendingApproval",
                "approval",
                "approvalId",
            ],
        )
        self._emit_run_event(
            failed,
            "AgentRunFailed",
            message,
            {"error": message, "reason": reason, "retryable": retryable},
            "FailAgentRun",
            reason,
        )
        return failed

    def _parse_and_validate_decision(
        self,
        raw_decision: Any,
        agent: AgentResource,
        run: AgentRunResource,
        usage: dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(raw_decision, str):
            try:
                decision = json.loads(raw_decision)
            except json.JSONDecodeError as exc:
                raise DecisionValidationError(
                    "DecisionParseFailed", f"Decision response is not valid JSON: {exc}"
                ) from exc
        elif isinstance(raw_decision, dict):
            decision = raw_decision
        else:
            raise DecisionValidationError("DecisionParseFailed", "Decision response must be an object")

        if not isinstance(decision, dict):
            raise DecisionValidationError("DecisionValidationFailed", "Decision must be a JSON object")
        if decision.get("version") != "v1":
            raise DecisionValidationError("DecisionVersionUnsupported", "Decision version must be v1")
        decision_type = decision.get("type")
        if not isinstance(decision_type, str):
            raise DecisionValidationError("DecisionValidationFailed", "Decision type is required")
        if decision_type == "request_input":
            raise DecisionValidationError("DecisionTypeUnsupported", "request_input is not implemented")
        if decision_type not in {"invoke_tool", "complete", "fail"}:
            raise DecisionValidationError("DecisionTypeUnsupported", f"Unsupported Decision type: {decision_type}")

        if decision_type == "invoke_tool":
            tool = decision.get("tool")
            operation = decision.get("operation")
            arguments = decision.get("arguments")
            if not isinstance(tool, str) or not tool:
                raise DecisionValidationError("DecisionValidationFailed", "invoke_tool Decision requires tool")
            if not isinstance(operation, str) or not operation:
                raise DecisionValidationError("DecisionValidationFailed", "invoke_tool Decision requires operation")
            if not isinstance(arguments, dict):
                raise DecisionValidationError(
                    "DecisionValidationFailed", "invoke_tool Decision requires arguments object"
                )
            if tool not in agent.spec.tools:
                raise DecisionValidationError(
                    "CapabilityViolation", f"Agent {agent.metadata.name} cannot use Tool {tool}"
                )
            if usage["toolInvocations"] >= run.spec.execution.maxToolInvocations:
                raise DecisionValidationError("DecisionValidationFailed", "ToolInvocation budget is exhausted")
            try:
                _tool, operation_spec = self._tool_and_operation(tool, operation)
                self._validate_arguments(arguments, operation_spec, f"Decision {tool}.{operation} arguments")
            except DecisionValidationError:
                raise
            except Exception as exc:
                raise DecisionValidationError("ToolArgumentsInvalid", str(exc)) from exc
            return {
                "version": "v1",
                "type": "invoke_tool",
                "tool": tool,
                "operation": operation,
                "arguments": arguments,
            }

        if decision_type == "complete":
            summary = decision.get("summary")
            outputs = decision.get("outputs")
            if not isinstance(summary, str) or not summary.strip():
                raise DecisionValidationError("DecisionValidationFailed", "complete Decision requires summary")
            if not isinstance(outputs, list):
                raise DecisionValidationError("DecisionValidationFailed", "complete Decision requires outputs array")
            if any(not isinstance(item, dict) for item in outputs):
                raise DecisionValidationError("DecisionValidationFailed", "complete Decision outputs must be objects")
            return {"version": "v1", "type": "complete", "summary": summary, "outputs": outputs}

        reason = decision.get("reason")
        retryable = decision.get("retryable")
        if not isinstance(reason, str) or not reason.strip():
            raise DecisionValidationError("DecisionValidationFailed", "fail Decision requires reason")
        if not isinstance(retryable, bool):
            raise DecisionValidationError("DecisionValidationFailed", "fail Decision requires retryable boolean")
        return {"version": "v1", "type": "fail", "reason": reason, "retryable": retryable}

    @staticmethod
    def _decision_summary(decision: dict[str, Any]) -> dict[str, Any]:
        decision_type = str(decision.get("type"))
        if decision_type == "invoke_tool":
            return {
                "type": "invoke_tool",
                "version": decision.get("version"),
                "tool": decision.get("tool"),
                "operation": decision.get("operation"),
            }
        if decision_type == "complete":
            return {
                "type": "complete",
                "version": decision.get("version"),
                "summary": AgentRuntime._redact_text(str(decision.get("summary") or "")),
                "outputCount": len(decision.get("outputs") or []),
            }
        if decision_type == "fail":
            return {
                "type": "fail",
                "version": decision.get("version"),
                "reason": decision.get("reason"),
                "retryable": decision.get("retryable"),
            }
        return {"type": decision_type}

    @staticmethod
    def _redact_text(value: str, limit: int = 240) -> str:
        compact = " ".join(value.split())
        if len(compact) <= limit:
            return compact
        return f"{compact[: limit - 3]}..."

    def _tool_and_operation(self, tool_name: str, operation_name: str) -> tuple[ToolResource, ToolOperationSpec]:
        manifest = self.store.get(ResourceKind.TOOL, tool_name)
        if manifest is None:
            raise DecisionValidationError("CapabilityViolation", f"Tool {tool_name} not found")
        tool = parse_resource(manifest)
        if not isinstance(tool, ToolResource):
            raise TypeError(f"expected ToolResource, got {type(tool).__name__}")
        if not tool.spec.operations:
            raise DecisionValidationError(
                "CapabilityViolation", f"Tool {tool_name} does not define supported operations"
            )
        operation = next((candidate for candidate in tool.spec.operations if candidate.name == operation_name), None)
        if operation is None:
            raise DecisionValidationError(
                "CapabilityViolation",
                f"Tool {tool_name} does not support operation {operation_name}",
            )
        return tool, operation

    def _validate_arguments(
        self,
        arguments: dict[str, Any],
        operation: ToolOperationSpec,
        path: str,
    ) -> None:
        if operation.inputSchema:
            self._validate_schema_value(arguments, operation.inputSchema, path)

    def _validate_schema_value(self, value: Any, schema: dict[str, Any], path: str) -> None:
        expected_type = schema.get("type")
        if expected_type is not None and not self._schema_type_matches(value, expected_type):
            expected = ", ".join(expected_type) if isinstance(expected_type, list) else str(expected_type)
            raise DecisionValidationError("ToolArgumentsInvalid", f"{path} must be {expected}")
        enum = schema.get("enum")
        if isinstance(enum, list) and value not in enum:
            raise DecisionValidationError("ToolArgumentsInvalid", f"{path} must be one of: {', '.join(map(str, enum))}")
        if schema.get("type") == "object" or isinstance(value, dict):
            if not isinstance(value, dict):
                raise DecisionValidationError("ToolArgumentsInvalid", f"{path} must be object")
            required = schema.get("required") or []
            if not isinstance(required, list):
                raise DecisionValidationError("ToolArgumentsInvalid", f"{path} schema required must be a list")
            for field_name in required:
                if not isinstance(field_name, str):
                    raise DecisionValidationError(
                        "ToolArgumentsInvalid", f"{path} schema required entries must be strings"
                    )
                if field_name not in value:
                    raise DecisionValidationError("ToolArgumentsInvalid", f"{path}.{field_name} is required")
            properties = schema.get("properties") or {}
            if not isinstance(properties, dict):
                raise DecisionValidationError("ToolArgumentsInvalid", f"{path} schema properties must be an object")
            for field_name, property_schema in properties.items():
                if field_name in value and isinstance(property_schema, dict):
                    self._validate_schema_value(value[field_name], property_schema, f"{path}.{field_name}")
        if schema.get("type") == "array" or isinstance(value, list):
            if not isinstance(value, list):
                raise DecisionValidationError("ToolArgumentsInvalid", f"{path} must be array")
            items_schema = schema.get("items")
            if items_schema is not None and not isinstance(items_schema, dict):
                raise DecisionValidationError("ToolArgumentsInvalid", f"{path} schema items must be an object")
            if isinstance(items_schema, dict):
                for index, item in enumerate(value):
                    self._validate_schema_value(item, items_schema, f"{path}[{index}]")

    @staticmethod
    def _schema_type_matches(value: Any, expected_type: Any) -> bool:
        if isinstance(expected_type, list):
            return any(AgentRuntime._schema_type_matches(value, item) for item in expected_type)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "null":
            return value is None
        return True

    @staticmethod
    def _validate_outputs(mission: MissionResource, decision: dict[str, Any]) -> None:
        required_outputs = {name for name, enabled in mission.spec.outputs.items() if enabled}
        outputs = decision.get("outputs") or []
        if required_outputs and not outputs:
            raise RuntimeError("Completion Decision did not include required outputs")
        output_names = {
            str(item.get("name") or item.get("type") or item.get("ref")) for item in outputs if isinstance(item, dict)
        }
        missing = sorted(required_outputs - output_names)
        if missing:
            raise RuntimeError(f"Completion Decision missing required outputs: {', '.join(missing)}")

    @staticmethod
    def _tool_invocation_name(run: AgentRunResource, frame: dict[str, Any]) -> str:
        generation = run.status.observedGeneration or run.metadata.generation
        return f"{run.metadata.name}-tool-{generation}-{int(frame['iteration']):04d}-1"

    @staticmethod
    def _idempotency_key(run: AgentRunResource, frame: dict[str, Any], decision: dict[str, Any]) -> str:
        payload = {
            "agentRun": run.metadata.name,
            "generation": run.status.observedGeneration or run.metadata.generation,
            "iteration": frame.get("iteration"),
            "decision": decision,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _tool_invocation(self, name: str, namespace: str | None) -> ToolInvocationResource:
        manifest = self.store.get(ResourceKind.TOOL_INVOCATION, name, namespace)
        if manifest is None:
            raise RuntimeError(f"ToolInvocation {name} not found")
        invocation = parse_resource(manifest)
        if not isinstance(invocation, ToolInvocationResource):
            raise TypeError(f"expected ToolInvocationResource, got {type(invocation).__name__}")
        return invocation

    def _approval_is_pending(self, invocation: ToolInvocationResource) -> bool:
        approval_id = invocation.status.data.get("approvalId") or invocation.status.data.get("approval")
        if not isinstance(approval_id, str):
            return False
        approval = self.store.get(ResourceKind.APPROVAL, approval_id)
        return bool(approval and (approval.get("status") or {}).get("phase") == "Pending")

    async def _execute_runtime(
        self,
        runtime: ToolRuntime,
        invocation: ToolInvocationResource,
        tool: ToolResource,
        operation: ToolOperationSpec,
    ) -> Observation:
        execute = asyncio.to_thread(runtime.execute, invocation)
        timeout_seconds = self._effective_timeout(invocation, tool, operation)
        if timeout_seconds is None:
            return await execute
        try:
            return await asyncio.wait_for(execute, timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise TimeoutError from exc

    @staticmethod
    def _effective_timeout(
        invocation: ToolInvocationResource,
        tool: ToolResource,
        operation: ToolOperationSpec,
    ) -> float | None:
        return invocation.spec.timeoutSeconds or operation.timeoutSeconds or tool.spec.timeoutSeconds

    def _runtime_action(
        self,
        invocation: ToolInvocationResource,
        run: AgentRunResource,
        agent: AgentResource,
    ) -> RuntimeAction:
        return RuntimeAction(
            tool=invocation.spec.tool,
            operation=invocation.spec.operation,
            details={
                "toolInvocation": invocation.metadata.name,
                "arguments": invocation.spec.arguments,
            },
            workspace=invocation.metadata.namespace,
            mission=run.spec.missionRef.name,
            agent=agent.metadata.name,
            agentRun=run.metadata.name,
            correlation_id=run_correlation_id(run),
        )

    def _set_tool_status(
        self,
        invocation: ToolInvocationResource,
        run: AgentRunResource,
        phase: str,
        message: str,
        event_type: str,
        data: dict[str, Any],
        *,
        observation: Observation | None = None,
    ) -> None:
        self.store.update_status(
            ResourceKind.TOOL_INVOCATION,
            invocation.metadata.name,
            invocation.metadata.namespace,
            phase,
            message,
            {**self._tool_event_payload(invocation, run), **data},
            event_type=event_type,
            event_context=self._tool_context(invocation, run, event_type, phase),
            observation=observation,
        )

    def _record_observation(
        self,
        invocation: ToolInvocationResource,
        run: AgentRunResource,
        observation: Observation,
    ) -> None:
        self.store.emit_event(
            "ObservationRecorded",
            ResourceKind.TOOL_INVOCATION,
            invocation.metadata.name,
            invocation.metadata.namespace,
            f"Observation recorded for ToolInvocation {invocation.metadata.name}",
            {
                **self._tool_event_payload(invocation, run),
                "observation": observation.model_dump(mode="json", exclude_none=True, exclude_defaults=True),
                "summary": observation.summary,
            },
            event_context=self._tool_context(invocation, run, "RecordObservation", "ObservationRecorded"),
        )

    @staticmethod
    def _tool_event_payload(invocation: ToolInvocationResource, run: AgentRunResource) -> dict[str, Any]:
        return {
            "workspace": invocation.metadata.namespace,
            "agentRun": run.metadata.name,
            "toolInvocation": invocation.metadata.name,
            "tool": invocation.spec.tool,
            "operation": invocation.spec.operation,
        }

    def _tool_context(
        self,
        invocation: ToolInvocationResource,
        run: AgentRunResource,
        action: str,
        reason: str,
    ) -> EventContext:
        return EventContext(
            controller="AgentRuntime",
            action=action,
            reason=reason,
            correlation_id=run_correlation_id(run),
            workspace=invocation.metadata.namespace,
            mission=run.spec.missionRef.name,
        )

    @staticmethod
    def _error_observation(reason: str, message: str) -> Observation:
        return Observation(
            summary=message,
            error=ObservationError(reason=reason, message=message),
        )

    def _emit_retry_scheduled(
        self,
        run: AgentRunResource,
        frame: dict[str, Any],
        reason: str,
        message: str,
    ) -> None:
        self._emit_run_event(
            run,
            "ExecutionRetryScheduled",
            message,
            {
                "iteration": frame.get("iteration"),
                "retryCount": frame.get("retryCount", 0),
                "modelRetryCount": frame.get("modelRetryCount", 0),
                "invalidDecisionRetryCount": frame.get("invalidDecisionRetryCount", 0),
                "toolRetryCount": frame.get("toolRetryCount", 0),
                "reason": reason,
            },
            "ScheduleRetry",
            reason,
        )
