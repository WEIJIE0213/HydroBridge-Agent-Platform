from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Chunk:
    id: str
    achievement_id: str
    version: int
    field: str
    ordinal: int
    text: str
    start: int
    end: int
    content_hash: str
    document_id: str | None = None
    locator: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


FIELDS = (
    "title",
    "abstract",
    "technical_principle",
    "application_scenarios",
    "advantages",
    "limitations",
    "metrics",
    "cost_description",
    "maturity_level",
    "organizations",
    "tags",
)


def field_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(field_text(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def chunk_achievement(achievement: Any, size: int = 700, overlap: int = 100) -> list[Chunk]:
    """Split within source fields; offsets always address the exact unmodified field text."""
    if size < 1 or not 0 <= overlap < size:
        raise ValueError("Chunk size must exceed overlap")
    chunks = []
    fields = [(field, field_text(getattr(achievement, field, None)), None, "") for field in FIELDS]
    # Keep original text and locators in PG. Derived summaries never replace source evidence.
    for document in (getattr(achievement, "structured_data", None) or {}).get(
        "source_documents", []
    ):
        if not document.get("index", True):
            continue
        for index, segment in enumerate(document["segments"]):
            if not segment.get("index", True):
                continue
            fields.append(
                (
                    f"document:{document['sha256'][:16]}:{index}",
                    segment["text"],
                    document["document_id"],
                    segment["locator"],
                )
            )
    for field, text, document_id, locator in fields:
        start = 0
        while start < len(text):
            end = min(start + size, len(text))
            if end < len(text):
                boundaries = [text.rfind(mark, start + size // 2, end) for mark in "。；\n"]
                boundary = max(boundaries)
                if boundary >= start + size // 2:
                    end = boundary + 1
            passage = text[start:end]
            digest = hashlib.sha256(passage.encode()).hexdigest()
            ordinal = len(chunks)
            identity = f"{achievement.id}:{achievement.version}:{field}:{start}:{digest}"
            chunks.append(
                Chunk(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, identity)),
                    achievement_id=achievement.id,
                    version=achievement.version,
                    field=field,
                    ordinal=ordinal,
                    text=passage,
                    start=start,
                    end=end,
                    content_hash=digest,
                    document_id=document_id,
                    locator=locator,
                )
            )
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks
