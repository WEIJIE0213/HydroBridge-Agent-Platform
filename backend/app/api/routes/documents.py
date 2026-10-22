from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.api.deps import CurrentUser, DatabaseSession, Services
from app.db.models.knowledge import Document

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    session: DatabaseSession,
    user: CurrentUser,
    services: Services,
) -> Response:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="原始文档不存在")
    if not user.is_superuser and (
        document.organization_id is None or document.organization_id != user.organization_id
    ):
        raise HTTPException(status_code=403, detail="无权访问该原始文档")
    content = await services.object_storage.get_bytes(
        services.object_storage.document_bucket,
        document.object_key,
    )
    return Response(
        content=content,
        media_type=document.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(document.filename)}",
            "X-Content-SHA256": document.sha256,
        },
    )
