import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_current_user, get_supabase
from app.core.limiter import limiter
from app.modules.auth.schemas import AuthUser
from app.modules.support.schemas import SupportInquiryCreate, SupportInquirySubmitResponse
from app.modules.support.service import SupportService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/support", tags=["Support"])


def get_support_service(
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> SupportService:
    return SupportService(supabase, settings)


@router.post("/contact", response_model=SupportInquirySubmitResponse)
@limiter.limit("5/minute")
async def submit_support_inquiry(
    request: Request,
    body: SupportInquiryCreate,
    user: AuthUser = Depends(get_current_user),
    service: SupportService = Depends(get_support_service),
):
    try:
        return service.submit_inquiry(user, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("Failed to submit support inquiry for user_id=%s", user.id)
        raise HTTPException(status_code=500, detail="Failed to submit support inquiry")
