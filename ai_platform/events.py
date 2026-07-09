from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

CORRELATION_ID_ANNOTATION = "ai.platform/correlation-id"
CORRELATION_ID_STATUS_KEY = "correlationId"


@dataclass(frozen=True)
class EventContext:
    controller: str
    action: str
    reason: str
    correlation_id: str | None = None
    workspace: str | None = None
    mission: str | None = None


def new_correlation_id() -> str:
    return str(uuid4())


def correlation_id_from_manifest(manifest: dict[str, Any]) -> str | None:
    metadata = manifest.get("metadata") or {}
    annotations = metadata.get("annotations") or {}
    annotated = annotations.get(CORRELATION_ID_ANNOTATION)
    if isinstance(annotated, str):
        return annotated
    status = manifest.get("status") or {}
    data = status.get("data") or {}
    value = data.get(CORRELATION_ID_STATUS_KEY)
    return value if isinstance(value, str) else None
