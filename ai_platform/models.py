from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from .resources import ModelConfig
from .storage import ResourceStore

Message = dict[str, str]
JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ModelResponse:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderAdapterError(RuntimeError):
    def __init__(self, reason: str, message: str, metadata: dict[str, Any]) -> None:
        self.reason = reason
        self.metadata = {**metadata, "normalizationOutcome": "rejected", "adapterErrorReason": reason}
        super().__init__(message)


class ModelClient(ABC):
    @abstractmethod
    async def generate(self, messages: list[Message]) -> str | ModelResponse:
        raise NotImplementedError


class StubModelClient(ModelClient):
    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    async def generate(self, messages: list[Message]) -> str | ModelResponse:
        user_content = "\n\n".join(message["content"] for message in messages if message["role"] == "user")
        artifact = (
            "# Agent Result\n\n"
            "This artifact was produced by the stub model.\n\n"
            "## Interpreted Objective\n\n"
            f"{user_content.strip()}\n\n"
            "## Proposed Next Steps\n\n"
            "1. Refine the mission brief.\n"
            "2. Replace the stub model with an OpenAI-compatible endpoint for real execution.\n"
            "3. Add domain-specific tools to the Agent spec.\n"
        )
        outputs = [
            {"type": output_name, "ref": f"stub://outputs/{output_name}"}
            for output_name in self._requested_outputs(user_content)
        ]
        return json.dumps(
            {
                "version": "v1",
                "type": "complete",
                "summary": artifact,
                "outputs": outputs,
            },
            sort_keys=True,
        )

    @staticmethod
    def _requested_outputs(user_content: str) -> list[str]:
        for line in user_content.splitlines():
            if not line.startswith("Requested outputs:"):
                continue
            _, _, raw_outputs = line.partition(":")
            return [item.strip() for item in raw_outputs.split(",") if item.strip()]
        return []


class OpenAICompatibleClient(ModelClient):
    def __init__(self, config: ModelConfig, store: ResourceStore | None = None) -> None:
        base_url = config.baseUrl
        if not base_url:
            raise ValueError("openai-compatible model requires baseUrl")
        self.config = config
        self.store = store
        self.base_url = base_url.rstrip("/")

    async def generate(self, messages: list[Message]) -> str | ModelResponse:
        return await asyncio.to_thread(self._generate_sync, messages)

    def _generate_sync(self, messages: list[Message]) -> ModelResponse:
        api_key = os.environ.get(self.config.apiKeyEnv)
        if not api_key:
            raise RuntimeError(f"missing API key environment variable: {self.config.apiKeyEnv}")

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = monotonic()
        request_id: str | None = None
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeoutSeconds) as response:
                request_id = response.headers.get("x-request-id") or response.headers.get("request-id")
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"model endpoint returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"model endpoint unavailable: {exc.reason}") from exc

        latency_ms = round((monotonic() - started) * 1000, 3)
        metadata = self._response_metadata(body, request_id, latency_ms)
        try:
            choice = body["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderAdapterError(
                "UnknownResponseShape",
                "model endpoint response did not match OpenAI chat completions format",
                metadata,
            ) from exc

        content = message.get("content")
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            metadata["responseMode"] = "tool_calls"
            metadata["toolCallCount"] = len(tool_calls)
            first_call = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
            metadata["nativeCallId"] = first_call.get("id") if isinstance(first_call.get("id"), str) else None
            normalized, call_id = normalize_native_tool_calls(tool_calls, metadata)
            metadata["nativeCallId"] = call_id
            metadata["normalizationOutcome"] = "normalized"
            return ModelResponse(normalized, metadata)
        if not isinstance(content, str) or not content.strip():
            metadata["responseMode"] = "tool_calls" if isinstance(tool_calls, list) else "content"
            metadata["toolCallCount"] = len(tool_calls) if isinstance(tool_calls, list) else 0
            raise ProviderAdapterError(
                "EmptyProviderResponse",
                "provider response contained neither Decision content nor a native tool call",
                metadata,
            )
        metadata.update({"responseMode": "content", "toolCallCount": len(tool_calls or [])})
        metadata["normalizationOutcome"] = "normalized"
        return ModelResponse(normalize_decision_content(content), metadata)

    def _response_metadata(self, body: Any, request_id: str | None, latency_ms: float) -> dict[str, Any]:
        payload: dict[str, Any] = body if isinstance(body, dict) else {}
        raw_choices = payload.get("choices")
        choices: list[Any] = raw_choices if isinstance(raw_choices, list) else []
        choice: dict[str, Any] = choices[0] if choices and isinstance(choices[0], dict) else {}
        raw_usage = payload.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        details = usage.get("completion_tokens_details")
        if not isinstance(details, dict):
            details = {}
        return {
            "provider": self.config.provider,
            "model": payload.get("model") or self.config.model,
            "requestId": request_id,
            "responseId": payload.get("id"),
            "finishReason": choice.get("finish_reason"),
            "latencyMs": latency_ms,
            "promptTokenCount": usage.get("prompt_tokens"),
            "completionTokenCount": usage.get("completion_tokens"),
            "reasoningTokenCount": details.get("reasoning_tokens") or usage.get("reasoning_tokens"),
        }


def normalize_native_tool_calls(tool_calls: object, metadata: dict[str, Any] | None = None) -> tuple[str, str | None]:
    event_metadata = metadata or {}
    if not isinstance(tool_calls, list):
        raise ProviderAdapterError("UnknownToolCallShape", "native tool_calls must be an array", event_metadata)
    if len(tool_calls) != 1:
        reason = "MultipleNativeToolCallsUnsupported" if tool_calls else "EmptyProviderResponse"
        raise ProviderAdapterError(
            reason,
            f"expected exactly one native tool call, received {len(tool_calls)}",
            event_metadata,
        )
    call = tool_calls[0]
    if not isinstance(call, dict) or call.get("type") != "function" or not isinstance(call.get("function"), dict):
        raise ProviderAdapterError(
            "UnsupportedToolCallType",
            "native tool call must have type function",
            event_metadata,
        )
    function = call["function"]
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise ProviderAdapterError("NativeToolNameMissing", "native function name is required", event_metadata)
    parts = name.split(".")
    if len(parts) not in {1, 2} or not all(parts):
        raise ProviderAdapterError(
            "NativeToolNameAmbiguous",
            "native function name must use the unambiguous <tool>.<operation> form",
            event_metadata,
        )
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ProviderAdapterError(
                "NativeToolArgumentsMalformed",
                "native tool arguments are not valid JSON",
                event_metadata,
            ) from exc
    if not isinstance(arguments, dict):
        raise ProviderAdapterError(
            "NativeToolArgumentsInvalid",
            "native tool arguments must be an object",
            event_metadata,
        )
    arguments = dict(arguments)
    tool_name = parts[0]
    operation_name = parts[1] if len(parts) == 2 else "invoke_tool"
    if operation_name == "invoke_tool":
        embedded_operation = arguments.pop("operation", None)
        if not isinstance(embedded_operation, str) or not embedded_operation:
            raise ProviderAdapterError(
                "NativeToolOperationMissing",
                "<tool>.invoke_tool requires an operation string in arguments",
                event_metadata,
            )
        operation_name = embedded_operation
    decision = {
        "version": "v1",
        "type": "invoke_tool",
        "tool": tool_name,
        "operation": operation_name,
        "arguments": arguments,
    }
    call_id = call.get("id") if isinstance(call.get("id"), str) else None
    return json.dumps(decision, sort_keys=True), call_id


def normalize_decision_content(content: str) -> str:
    stripped = content.strip()
    match = JSON_FENCE_PATTERN.match(stripped)
    normalized = match.group("body").strip() if match else stripped
    payload = _load_decision_json(normalized)
    if payload is None:
        return normalized
    if not isinstance(payload, dict):
        return normalized
    decision = payload.get("decision")
    if isinstance(decision, dict):
        payload = decision
    canonical = _canonical_decision_payload(payload)
    return json.dumps(canonical, sort_keys=True) if canonical is not None else normalized


def _load_decision_json(content: str) -> object | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        candidate = _first_json_object(content)
        if candidate is None:
            return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _first_json_object(content: str) -> str | None:
    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(content[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return content[start : index + 1]
            if depth < 0:
                return None
    return None


def _canonical_decision_payload(payload: dict[str, object]) -> dict[str, object] | None:
    decision_type = payload.get("type")
    if decision_type is None and isinstance(payload.get("tool"), str) and isinstance(payload.get("operation"), str):
        decision_type = "invoke_tool"
    if decision_type not in {"invoke_tool", "complete", "fail"}:
        return None

    canonical = dict(payload)
    canonical["version"] = canonical.get("version") or "v1"
    canonical["type"] = decision_type
    if decision_type == "invoke_tool":
        arguments = canonical.get("arguments")
        if arguments is None or arguments == []:
            canonical["arguments"] = {}
    if decision_type == "complete":
        canonical["outputs"] = canonical.get("outputs") or []
    if decision_type == "fail":
        canonical["reason"] = canonical.get("reason") or canonical.get("message") or "Model returned fail Decision"
        canonical["retryable"] = canonical.get("retryable") if isinstance(canonical.get("retryable"), bool) else False
    return canonical


def build_model_client(config: ModelConfig, store: ResourceStore | None = None) -> ModelClient:
    if config.provider == "stub":
        return StubModelClient(config)
    if config.provider == "openai-compatible":
        return OpenAICompatibleClient(config, store=store)
    raise ValueError(f"unsupported model provider: {config.provider}")
