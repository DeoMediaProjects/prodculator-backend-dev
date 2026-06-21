import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import Settings, get_settings
from app.core.database_client import DatabaseClient
from app.core.dependencies import get_supabase
from app.core.limiter import limiter
from app.modules.contact.schemas import ContactMessageCreate, ContactMessageSubmitResponse
from app.modules.contact.service import ContactService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/contact", tags=["Contact"])


def get_contact_service(
    supabase: DatabaseClient = Depends(get_supabase),
    settings: Settings = Depends(get_settings),
) -> ContactService:
    return ContactService(supabase, settings)


@router.post("", response_model=ContactMessageSubmitResponse)
@limiter.limit("5/minute")
async def submit_contact_message(
    request: Request,
    body: ContactMessageCreate,
    service: ContactService = Depends(get_contact_service),
):
    # Public endpoint — no authentication. The contact form is reachable by
    # prospects and logged-out visitors, so we deliberately do not require a user.
    try:
        return service.submit_message(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("Failed to submit contact message")
        raise HTTPException(status_code=500, detail="Failed to submit contact message")
