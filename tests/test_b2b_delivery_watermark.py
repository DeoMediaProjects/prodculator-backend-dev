"""Per-recipient watermarking and client-side recipient management (SOW 4.4).

Each recipient gets their OWN render stamped with their address, so a leaked
report is traceable to the copy it came from. Watermarking must never be a
reason a paid-for report goes undelivered.
"""
from unittest.mock import MagicMock

from app.core.config import Settings
from app.modules.b2b.service import B2BService


def _service():
    svc = B2BService(MagicMock(), Settings(_env_file=None, JWT_SECRET_KEY="x" * 64))
    svc.email_service = MagicMock()
    svc.pdf_service = MagicMock()
    return svc


def _request_row(**overrides):
    row = {
        "id": "req-1",
        "product_type": "camera_equipment",
        "period_start": "2026-06-01",
        "period_end": "2026-06-30",
        "recipient_email": "dan@greyconsortium.co.uk",
        "extra_recipient_email": "insights@greyconsortium.co.uk",
        "metrics": {"title": "Strategic Production Trend Intelligence"},
    }
    row.update(overrides)
    return row


def _sent_to(svc):
    return [c.args[0] for c in svc.email_service.send.call_args_list]


def _attachments(svc):
    return [c.kwargs["attachments"][0]["content"] for c in svc.email_service.send.call_args_list]


# ------------------------------------------------------------ recipient list


def test_recipients_include_primary_and_extra():
    svc = _service()
    assert svc.recipients_for(_request_row()) == [
        "dan@greyconsortium.co.uk",
        "insights@greyconsortium.co.uk",
    ]


def test_duplicate_recipient_is_not_sent_twice():
    """Primary and extra can legitimately be the same address."""
    svc = _service()
    row = _request_row(extra_recipient_email="DAN@greyconsortium.co.uk")

    assert svc.recipients_for(row) == ["dan@greyconsortium.co.uk"]


def test_missing_extra_recipient_is_skipped():
    svc = _service()
    assert svc.recipients_for(_request_row(extra_recipient_email=None)) == [
        "dan@greyconsortium.co.uk"
    ]


# --------------------------------------------------------------- watermarking


def test_each_recipient_gets_their_own_watermarked_render():
    svc = _service()
    rendered: list[str] = []

    def fake_render(metrics):
        rendered.append(metrics.get("watermark_recipient"))
        return f"<html>{metrics.get('watermark_recipient')}</html>"

    svc.render_pdf_html = fake_render
    svc.pdf_service.generate_pdf_bytes.side_effect = lambda html: html.encode()

    svc.deliver_request_pdf(_request_row())

    # One render per recipient, each stamped with that recipient.
    assert rendered == ["dan@greyconsortium.co.uk", "insights@greyconsortium.co.uk"]
    # And the two attachments therefore differ.
    attachments = _attachments(svc)
    assert len(attachments) == 2
    assert attachments[0] != attachments[1]


def test_watermark_does_not_mutate_the_stored_metrics():
    """The stored metrics must stay recipient-neutral."""
    svc = _service()
    row = _request_row()
    metrics = row["metrics"]
    svc.render_pdf_html = MagicMock(return_value="<html></html>")
    svc.pdf_service.generate_pdf_bytes.return_value = b"%PDF"

    svc.deliver_request_pdf(row)

    assert "watermark_recipient" not in metrics


def test_delivery_falls_back_to_unwatermarked_copy_when_watermarking_fails():
    """A watermarking failure must not withhold a paid-for report."""
    svc = _service()
    svc.render_pdf_html = MagicMock(side_effect=RuntimeError("template exploded"))
    svc.download_request_pdf = MagicMock(return_value=b"%PDF original")

    recipients = svc.deliver_request_pdf(_request_row())

    assert len(recipients) == 2
    assert len(_sent_to(svc)) == 2
    svc.download_request_pdf.assert_called_once()  # fetched once, reused


def test_delivery_uses_supplied_bytes_as_fallback_without_refetching():
    svc = _service()
    svc.render_pdf_html = MagicMock(side_effect=RuntimeError("nope"))
    svc.download_request_pdf = MagicMock()

    svc.deliver_request_pdf(_request_row(), pdf_bytes=b"%PDF supplied")

    svc.download_request_pdf.assert_not_called()
    assert len(_sent_to(svc)) == 2


def test_request_with_no_metrics_still_delivers():
    """Older requests stored before metrics existed must not break delivery."""
    svc = _service()
    svc.download_request_pdf = MagicMock(return_value=b"%PDF")
    svc.render_pdf_html = MagicMock()

    svc.deliver_request_pdf(_request_row(metrics=None))

    svc.render_pdf_html.assert_not_called()  # nothing to watermark from
    assert len(_sent_to(svc)) == 2


def test_watermarked_pdf_passes_recipient_into_the_template():
    svc = _service()
    svc.render_pdf_html = MagicMock(return_value="<html></html>")
    svc.pdf_service.generate_pdf_bytes.return_value = b"%PDF"

    svc.watermarked_pdf({"title": "X"}, "dan@greyconsortium.co.uk")

    passed = svc.render_pdf_html.call_args.args[0]
    assert passed["watermark_recipient"] == "dan@greyconsortium.co.uk"
    assert passed["title"] == "X"


def test_delivery_marks_delivered_at():
    svc = _service()
    svc.render_pdf_html = MagicMock(return_value="<html></html>")
    svc.pdf_service.generate_pdf_bytes.return_value = b"%PDF"

    svc.deliver_request_pdf(_request_row())

    payloads = [c.args[0] for c in svc.db.table.return_value.update.call_args_list]
    assert any("delivered_at" in p for p in payloads)


# ------------------------------------------------- client recipient management


def test_set_extra_recipient_updates_own_subscription():
    svc = _service()
    svc.get_subscription = MagicMock(return_value={"id": "sub-1", "user_id": "user-1"})
    svc.db.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
        {"id": "sub-1", "user_id": "user-1", "extra_recipient_email": "analyst@studio.com"}
    ]

    result = svc.set_extra_recipient("sub-1", "user-1", "Analyst@Studio.com")

    assert result["extra_recipient_email"] == "analyst@studio.com"


def test_set_extra_recipient_refuses_another_users_subscription():
    """Ownership is enforced in the service, not just the router."""
    svc = _service()
    svc.get_subscription = MagicMock(return_value={"id": "sub-1", "user_id": "someone-else"})

    assert svc.set_extra_recipient("sub-1", "user-1", "x@y.com") is None
    svc.db.table.return_value.update.assert_not_called()


def test_set_extra_recipient_returns_none_for_missing_subscription():
    svc = _service()
    svc.get_subscription = MagicMock(return_value=None)

    assert svc.set_extra_recipient("nope", "user-1", "x@y.com") is None


def test_clearing_extra_recipient_is_supported():
    svc = _service()
    svc.get_subscription = MagicMock(return_value={"id": "sub-1", "user_id": "user-1"})
    svc.db.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []

    result = svc.set_extra_recipient("sub-1", "user-1", None)

    payload = svc.db.table.return_value.update.call_args.args[0]
    assert payload["extra_recipient_email"] is None
    assert result["extra_recipient_email"] is None  # falls back to merged row
