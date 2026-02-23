from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from app.core.database_client import DatabaseClient

from app.core.config import Settings, get_settings
from app.core.dependencies import get_supabase, get_current_user
from app.modules.auth.schemas import AuthUser
from app.modules.scripts.schemas import (
    ScriptAnalysisResult,
    ValidateFileResponse,
)
from app.modules.scripts.service import ScriptAnalysisService

router = APIRouter(prefix="/api/scripts", tags=["Scripts"])


def get_script_service(settings: Settings = Depends(get_settings)) -> ScriptAnalysisService:
    return ScriptAnalysisService(settings)


@router.post("/validate", response_model=ValidateFileResponse)
async def validate_script(
    file: UploadFile = File(...),
    service: ScriptAnalysisService = Depends(get_script_service),
):
    """Validate script file type and size."""
    valid, error = service.validate_file(file.filename or "", file.size or 0)
    return ValidateFileResponse(valid=valid, error=error)


@router.post("/upload")
async def upload_script(
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
    supabase: DatabaseClient = Depends(get_supabase),
    service: ScriptAnalysisService = Depends(get_script_service),
):
    """Upload a script file to Supabase Storage."""
    valid, error = service.validate_file(file.filename or "", file.size or 0)
    if not valid:
        raise HTTPException(status_code=400, detail=error)

    file_bytes = await file.read()
    ext = (file.filename or "script.txt").rsplit(".", 1)[-1].lower()
    import time

    storage_path = f"{user.id}/{int(time.time())}.{ext}"

    supabase.storage.from_("scripts").upload(
        storage_path,
        file_bytes,
        {"content-type": file.content_type or "application/octet-stream"},
    )

    return {"path": storage_path, "filename": file.filename}


@router.post("/analyze", response_model=ScriptAnalysisResult)
async def analyze_script(
    file: UploadFile = File(...),
    user: AuthUser = Depends(get_current_user),
    service: ScriptAnalysisService = Depends(get_script_service),
):
    """Upload and analyze a script file. Returns analysis result."""
    valid, error = service.validate_file(file.filename or "", file.size or 0)
    if not valid:
        raise HTTPException(status_code=400, detail=error)

    file_bytes = await file.read()
    try:
        text = service.extract_text(file.filename or "script.txt", file_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to extract text from file")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Script file appears to be empty")

    title = (file.filename or "Untitled").rsplit(".", 1)[0]
    try:
        return service.analyze(text, title)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Script analysis failed")
