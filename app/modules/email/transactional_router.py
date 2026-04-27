import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings
from app.modules.email.schemas import (
    TransactionalEmailErrorResponse,
    TransactionalEmailPreviewRequest,
    TransactionalEmailPreviewResponse,
    TransactionalEmailRequest,
    TransactionalEmailSuccessResponse,
)
from app.modules.email.service import EmailService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/emails", tags=["Emails"])


def get_email_service(settings: Settings = Depends(get_settings)) -> EmailService:
    return EmailService(settings)


@router.post(
    "/preview",
    response_model=TransactionalEmailPreviewResponse,
    responses={
        400: {"model": TransactionalEmailErrorResponse},
        500: {"model": TransactionalEmailErrorResponse},
    },
)
async def preview_transactional_email(
    body: TransactionalEmailPreviewRequest,
    service: EmailService = Depends(get_email_service),
):
    try:
        subject, html = service.render(template_name=body.template, context=body.data)
        return TransactionalEmailPreviewResponse(subject=subject, html=html)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content=TransactionalEmailErrorResponse(error=str(exc)).model_dump(),
        )
    except Exception:
        logger.exception("Failed to preview transactional email: template=%s", body.template)
        return JSONResponse(
            status_code=500,
            content=TransactionalEmailErrorResponse(error="Failed to render email preview").model_dump(),
        )


@router.post(
    "",
    response_model=TransactionalEmailSuccessResponse,
    responses={
        400: {"model": TransactionalEmailErrorResponse},
        500: {"model": TransactionalEmailErrorResponse},
    },
)
async def send_transactional_email(
    body: TransactionalEmailRequest,
    service: EmailService = Depends(get_email_service),
):
    try:
        service.send(
            to_email=str(body.to),
            template_name=body.template,
            context=body.data,
            attachments=[attachment.model_dump() for attachment in body.attachments],
        )
        return TransactionalEmailSuccessResponse()
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content=TransactionalEmailErrorResponse(error=str(exc)).model_dump(),
        )
    except Exception:
        logger.exception("Failed to send transactional email: template=%s to=%s", body.template, body.to)
        return JSONResponse(
            status_code=500,
            content=TransactionalEmailErrorResponse(error="Failed to send email").model_dump(),
        )
