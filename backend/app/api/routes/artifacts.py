from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.api.deps import CurrentUser, DatabaseSession, Services
from app.db.models.knowledge import Artifact
from app.schemas.artifact import ArtifactResponse

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


async def owned_artifact(
    artifact_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> Artifact:
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="成果文件不存在")
    if artifact.user_id != user.id and not user.is_superuser:
        raise HTTPException(status_code=403, detail="无权访问该成果文件")
    return artifact


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: str,
    session: DatabaseSession,
    user: CurrentUser,
) -> Artifact:
    return await owned_artifact(artifact_id, session, user)


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    session: DatabaseSession,
    user: CurrentUser,
    services: Services,
) -> Response:
    artifact = await owned_artifact(artifact_id, session, user)
    try:
        content = await services.object_storage.get_bytes(
            services.object_storage.artifact_bucket,
            artifact.object_key,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="成果文件存储暂时不可用") from exc
    filename = f"hydrobridge-report-{artifact.id}.md"
    return Response(
        content=content,
        media_type=artifact.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-SHA256": artifact.sha256,
        },
    )
