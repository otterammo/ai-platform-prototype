from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from .resources import ModelConfig
from .storage import ResourceStore

Message = dict[str, str]
JSON_FENCE_PATTERN = re.compile(r"^```(?:json)?\s*(?P<body>.*?)\s*```$", re.IGNORECASE | re.DOTALL)


class ModelClient(ABC):
    @abstractmethod
    async def generate(self, messages: list[Message]) -> str:
        raise NotImplementedError


class StubModelClient(ModelClient):
    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    async def generate(self, messages: list[Message]) -> str:
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

    async def generate(self, messages: list[Message]) -> str:
        return await asyncio.to_thread(self._generate_sync, messages)

    def _generate_sync(self, messages: list[Message]) -> str:
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
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeoutSeconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"model endpoint returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"model endpoint unavailable: {exc.reason}") from exc

        try:
            return normalize_decision_content(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("model endpoint response did not match OpenAI chat completions format") from exc


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
