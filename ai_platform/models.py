from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from .resources import ModelConfig
from .storage import ResourceStore

Message = dict[str, str]


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
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("model endpoint response did not match OpenAI chat completions format") from exc


def build_model_client(config: ModelConfig, store: ResourceStore | None = None) -> ModelClient:
    if config.provider == "stub":
        return StubModelClient(config)
    if config.provider == "openai-compatible":
        return OpenAICompatibleClient(config, store=store)
    raise ValueError(f"unsupported model provider: {config.provider}")
