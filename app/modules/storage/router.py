from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import get_settings

router = APIRouter(prefix="/api/storage", tags=["Storage"])


@router.get("/{bucket}/{file_path:path}")
async def get_storage_file(bucket: str, file_path: str):
    settings = get_settings()
    root = (Path(settings.STORAGE_ROOT) / bucket).resolve()
    target = (root / file_path).resolve()
    if not str(target).startswith(str(root)):
        raise HTTPException(status_code=400, detail="Invalid storage path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target)
