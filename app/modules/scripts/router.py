from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File


from app.core.config import Settings, get_settings
from app.core.dependencies import get_current_admin
from app.core.limiter import limiter
from app.modules.scripts.schemas import (
    ScriptAnalysisResult,
    ValidateFileResponse,
)
from app.modules.scripts.service import ScriptAnalysisService

router = APIRouter(prefix="/api/scripts", tags=["Scripts"])


def get_script_service(settings: Settings = Depends(get_settings)) -> ScriptAnalysisService:
    return ScriptAnalysisService(settings)


# Both routes in this module back the DEV-only /test/script-analysis tester
# (App.tsx gates that route on import.meta.env.DEV). The production upload flow
# goes through POST /api/reports, which does its own analysis and enforces plan
# quota. Neither route had a production caller, yet both were publicly
# reachable — /validate with no auth at all — so they are admin-only now. If an
# external client ever needs them, reinstate get_current_user plus a quota
# check rather than dropping the guard.
@router.post("/validate", response_model=ValidateFileResponse)
@limiter.limit("20/minute")
async def validate_script(
    request: Request,
    file: UploadFile = File(...),
    _admin=Depends(get_current_admin),
    service: ScriptAnalysisService = Depends(get_script_service),
):
    """Validate script file type and size."""
    valid, error = service.validate_file(file.filename or "", file.size or 0)
    return ValidateFileResponse(valid=valid, error=error)


# _analyze_chunked issues several model calls per script and carries no plan
# quota, so before this guard any authenticated account on any tier — including
# free — could run up the Anthropic bill unmetered. Admin-only for the reason
# above; the rate limit stays as a second ceiling.
@router.post("/analyze", response_model=ScriptAnalysisResult)
@limiter.limit("5/minute")
async def analyze_script(
    request: Request,
    file: UploadFile = File(...),
    _admin=Depends(get_current_admin),
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
