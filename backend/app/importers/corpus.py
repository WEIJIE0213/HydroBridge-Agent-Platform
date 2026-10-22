"""Import a reviewed local corpus package, preserving originals and idempotent provenance."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import func, select

from app.core.config import settings
from app.db.models.domain import Achievement, AchievementSource, Taxonomy
from app.db.models.identity import User
from app.db.models.knowledge import Document, DocumentChunk, OutboxEvent
from app.db.session import AsyncSessionFactory
from app.importers.water_data import FIELD_TO_TAXONOMY
from app.services.object_storage import ObjectStorageService


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


async def import_package(package: dict, session, storage, organization_id: str) -> dict:
    counts = Counter()
    await storage.ensure_buckets()
    documents = {
        d.sha256: d
        for d in (
            await session.scalars(
                select(Document).where(Document.organization_id == organization_id)
            )
        ).all()
    }
    for source in package["documents"]:
        digest = source["sha256"]
        if digest in documents:
            if documents[digest].id != source["document_id"]:
                raise ValueError("Existing document identity differs from package")
            counts["documents_unchanged"] += 1
            continue
        path = Path(source["local_path"])
        content = await asyncio.to_thread(path.read_bytes)
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"Original file digest mismatch: {source['filename']}")
        mime = mimetypes.guess_type(source["filename"])[0] or "application/octet-stream"
        key = f"corpus/{digest}/{source['filename']}"
        await storage.put_bytes(storage.document_bucket, key, content, mime)
        document = Document(
            id=source["document_id"],
            organization_id=organization_id,
            filename=source["filename"],
            mime_type=mime,
            size_bytes=len(content),
            sha256=digest,
            object_key=key,
            source_url=source["paths"][0],
            source_type="local_corpus",
            status="parsed",
            parser_version="water-corpus-v1",
        )
        session.add(document)
        documents[digest] = document
        counts["documents_created"] += 1
    await session.commit()
    existing = {
        a.normalized_title: a
        for a in (
            await session.scalars(
                select(Achievement).where(Achievement.organization_id == organization_id)
            )
        ).all()
    }
    taxonomies = dict((await session.execute(select(Taxonomy.code, Taxonomy.id))).all())
    source_rows = {
        (s.achievement_id, s.document_id): s
        for s in (
            await session.scalars(
                select(AchievementSource).where(
                    AchievementSource.document_id.in_([d.id for d in documents.values()])
                )
            )
        ).all()
    }
    chunk_ids = set((await session.scalars(select(DocumentChunk.id))).all())
    next_index = defaultdict(
        int,
        {
            doc: index + 1
            for doc, index in (
                await session.execute(
                    select(DocumentChunk.document_id, func.max(DocumentChunk.chunk_index)).group_by(
                        DocumentChunk.document_id
                    )
                )
            ).all()
        },
    )
    for number, record in enumerate(package["records"], 1):
        fields = record.get("normalized_fields", {})
        item = existing.get(record["normalized_title"])
        digest = fingerprint(record)
        if item is not None and (item.structured_data or {}).get("corpus_fingerprint") == digest:
            counts["achievements_unchanged"] += 1
            continue
        if item is None:
            original_text = next(
                (s["text"] for d in record["source_documents"] for s in d["segments"] if s["text"]),
                "",
            )
            taxonomy = next(
                (code for keyword, code in FIELD_TO_TAXONOMY.items() if keyword in record["title"]),
                "water-resource",
            )
            item = Achievement(
                id=str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL, organization_id + ":" + record["normalized_title"]
                    )
                ),
                organization_id=organization_id,
                title=record["title"],
                normalized_title=record["normalized_title"],
                taxonomy_id=taxonomies.get(taxonomy),
                abstract=fields.get("abstract") or original_text[:1500],
                technical_principle=fields.get("technical_principle", ""),
                application_scenarios=fields.get("application_scenarios", []),
                organizations=fields.get("organizations", []),
                maturity_level=fields.get("maturity_level"),
                advantages=fields.get("advantages", []),
                limitations=fields.get("limitations", []),
                metrics=fields.get("metrics", []),
                tags=fields.get("tags", []),
                cost_description=fields.get("cost_description", ""),
                evidence_score=0,
                status="approved",
                version=1,
            )
            session.add(item)
            existing[item.normalized_title] = item
            counts["achievements_created"] += 1
        else:
            item.version += 1
            counts["achievements_updated"] += 1
        item.structured_data = {
            **(item.structured_data or {}),
            "source_documents": record["source_documents"],
            "source_refs": record["source_refs"],
            "catalog_ids": record["catalog_ids"],
            "structured_variants": record["structured_variants"],
            "corpus_fingerprint": digest,
            "import_basis": "user_authorized_local_corpus",
            "maturity_basis": "source_explicit"
            if item.maturity_level is not None
            else "not_stated",
        }
        item.search_indexed = False
        await session.flush()
        for source in record["source_documents"]:
            document = documents[source["sha256"]]
            identity = (item.id, document.id)
            provenance = source_rows.get(identity)
            if provenance is None:
                provenance = AchievementSource(
                    achievement_id=item.id,
                    document_id=document.id,
                    canonical_url_hash=hashlib.sha256(document.sha256.encode()).hexdigest(),
                    title=document.filename,
                    url=f"{settings.api_prefix}/documents/{document.id}/download",
                    source_type="structured_json"
                    if document.filename.endswith(".json")
                    else "local_document",
                    publisher="用户提供的水利成果资料",
                    authority_level="user_supplied",
                    is_primary_source=not document.filename.endswith(".json"),
                    credibility_score=0,
                )
                session.add(provenance)
                source_rows[identity] = provenance
                counts["source_links_created"] += 1
            provenance.evidence = [
                {"locator": s["locator"], "method": s.get("method", ""), "sha256": document.sha256}
                for s in source["segments"]
            ]
            for segment in source["segments"]:
                content_hash = hashlib.sha256(segment["text"].encode()).hexdigest()
                chunk_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{document.id}:{item.id}:{segment['locator']}:{content_hash}",
                    )
                )
                if chunk_id in chunk_ids:
                    continue
                session.add(
                    DocumentChunk(
                        id=chunk_id,
                        document_id=document.id,
                        achievement_id=item.id,
                        chunk_index=next_index[document.id],
                        page_from=segment.get("page"),
                        page_to=segment.get("page"),
                        heading_path=[segment["locator"]],
                        content=segment["text"],
                        content_hash=content_hash,
                        indexed=False,
                        token_count=len(segment["text"]),
                    )
                )
                next_index[document.id] += 1
                chunk_ids.add(chunk_id)
                counts["source_segments_created"] += 1
        session.add(
            OutboxEvent(
                aggregate_type="achievement",
                aggregate_id=item.id,
                event_type="achievement.corpus_imported",
                version=item.version,
                payload={"achievement_id": item.id, "corpus_fingerprint": digest},
            )
        )
        if number % 100 == 0:
            await session.commit()
            print(f"Imported {number}/{len(package['records'])}", flush=True)
    await session.commit()
    return dict(counts)


async def main_async(args):
    package = json.loads(await asyncio.to_thread(args.package.read_text, encoding="utf-8"))
    blocking = [
        a
        for a in package["audit"]
        if a["kind"] in {"error", "pending_extraction", "needs_title_review"}
    ]
    if blocking:
        raise ValueError(
            f"Corpus package has {len(blocking)} unresolved extraction/mapping entries"
        )
    async with AsyncSessionFactory() as session:
        user = await session.scalar(
            select(User).where(User.email == settings.bootstrap_admin_email)
        )
        result = await import_package(
            package, session, ObjectStorageService(), user.organization_id
        )
    await asyncio.to_thread(
        args.report.write_text, json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
