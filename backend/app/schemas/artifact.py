from __future__ import annotations

from typing import Any

from app.schemas.common import TimestampedModel


class ArtifactResponse(TimestampedModel):
    user_id: str | None
    agent_run_id: str | None
    artifact_type: str
    title: str
    mime_type: str
    sha256: str
    metadata_json: dict[str, Any]
