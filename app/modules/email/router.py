from fastapi import APIRouter, Depends, HTTPException

from app.core.config import Settings, get_settings
from app.core.dependencies import require_admin
from app.core.schemas import SuccessResponse
from app.modules.auth.schemas import AuthUser
from app.modules.email.schemas import (
    EmailPreviewRequest,
    EmailPreviewResponse,
    SendTestEmailRequest,
)
from app.modules.email.service import EmailService

router = APIRouter(prefix="/api/admin/email", tags=["Admin Email"])


def get_email_service(settings: Settings = Depends(get_settings)) -> EmailService:
    return EmailService(settings)


@router.post("/preview", response_model=EmailPreviewResponse)
async def preview_email(
    body: EmailPreviewRequest,
    _: AuthUser = Depends(require_admin),
    service: EmailService = Depends(get_email_service),
):
    try:
        subject, html = service.render(body.template_name, body.context)
        return EmailPreviewResponse(subject=subject, html=html)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to render email template")


@router.post("/send-test", response_model=SuccessResponse)
async def send_test_email(
    body: SendTestEmailRequest,
    _: AuthUser = Depends(require_admin),
    service: EmailService = Depends(get_email_service),
):
    try:
        service.send(body.to_email, body.template_name, body.context)
        return SuccessResponse(message="Test email processed")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to send test email")

