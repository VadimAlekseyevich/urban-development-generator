from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from backend.app.api.dependencies import UploadServiceDep
from backend.app.application.uploads import UploadTooLargeError
from backend.app.schemas.upload import ArtifactUploadRead

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("", response_model=ArtifactUploadRead, status_code=status.HTTP_201_CREATED)
def upload_artifact(
    service: UploadServiceDep,
    file: Annotated[UploadFile, File(description="Raw source artifact")],
) -> ArtifactUploadRead:
    try:
        result = service.upload(
            file.file,
            filename=file.filename,
            content_type=file.content_type,
            size_hint=file.size,
        )
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Upload exceeds the configured {exc.max_size_bytes} byte limit",
        ) from exc
    finally:
        file.file.close()

    return ArtifactUploadRead(
        artifact_id=result.artifact.id,
        key=result.stat.ref.key,
        filename=result.filename,
        state=result.stat.ref.state,
        size_bytes=result.stat.size_bytes,
        checksum=result.stat.checksum,
        content_type=result.stat.content_type,
    )
