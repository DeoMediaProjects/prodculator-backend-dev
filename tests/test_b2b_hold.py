"""Tests for B2B hold-and-notify: a scheduled/on-demand intelligence report is
HELD (not delivered empty, not marked failed) when the period lacks enough
consented signals to clear the privacy floor, and the client + ops are notified.

SOW 4.3: "hold-and-notify on insufficient data rather than delivering an empty
report".
"""
from unittest.mock import MagicMock

from app.core.config import Settings
from app.modules.b2b.service import B2BService


def _service():
    db = MagicMock()
    svc = B2BService(db, Settings(_env_file=None, JWT_SECRET_KEY="x" * 64, CONTACT_EMAIL="ops@example.com"))
    svc.email_service = MagicMock()
    svc.pdf_service = MagicMock()
    return svc, db


def _request_row():
    return {
        "id": "req-1",
        "product_type": "camera_equipment",
        "period_start": "2026-06-01",
        "period_end": "2026-06-30",
        "recipient_email": "client@studio.com",
        "extra_recipient_email": None,
    }


def _sent_templates(email_mock):
    return [c.args[1] for c in email_mock.send.call_args_list]


def _update_payloads(db):
    return [c.args[0] for c in db.table.return_value.update.call_args_list]


def test_process_request_holds_when_data_insufficient():
    svc, db = _service()
    svc.get_request = MagicMock(return_value=_request_row())
    svc.build_period_metrics = MagicMock(return_value={
        "insufficient_data": True,
        "source_signal_count": 3,
        "thresholds": {"minimum_overall_records": 10, "minimum_segment_records": 5},
        "title": "Camera & Equipment Demand Intelligence",
    })

    svc.process_request("req-1")

    # Held, not failed, not completed.
    statuses = [p.get("status") for p in _update_payloads(db)]
    assert "held" in statuses
    assert "failed" not in statuses and "completed" not in statuses

    # No report was generated or delivered.
    svc.pdf_service.generate_pdf_bytes.assert_not_called()

    # Client got the hold notice; ops got the alert.
    templates = _sent_templates(svc.email_service)
    assert "b2b_intelligence_held" in templates
    assert "b2b_admin_alert" in templates

    # The client hold notice went to the client's address.
    held_calls = [c for c in svc.email_service.send.call_args_list if c.args[1] == "b2b_intelligence_held"]
    assert held_calls and held_calls[0].args[0] == "client@studio.com"


def test_hold_notice_also_goes_to_extra_recipient():
    svc, db = _service()
    row = _request_row()
    row["extra_recipient_email"] = "analyst@studio.com"
    svc.get_request = MagicMock(return_value=row)
    svc.build_period_metrics = MagicMock(return_value={
        "insufficient_data": True,
        "source_signal_count": 0,
        "thresholds": {"minimum_overall_records": 10},
        "title": "Camera & Equipment Demand Intelligence",
    })

    svc.process_request("req-1")

    held_recipients = {
        c.args[0] for c in svc.email_service.send.call_args_list if c.args[1] == "b2b_intelligence_held"
    }
    assert held_recipients == {"client@studio.com", "analyst@studio.com"}


def test_process_request_generates_when_data_sufficient():
    svc, db = _service()
    svc.get_request = MagicMock(return_value=_request_row())
    svc.build_period_metrics = MagicMock(return_value={
        "insufficient_data": False,
        "source_signal_count": 40,
        "thresholds": {"minimum_overall_records": 10},
        "title": "Camera & Equipment Demand Intelligence",
        "sections": [{"title": "Territory", "rows": [{"label": "UK", "value": 12}]}],
    })
    svc.render_pdf_html = MagicMock(return_value="<html></html>")
    svc.pdf_service.generate_pdf_bytes.return_value = b"%PDF-1.4 fake"
    svc.storage_path = MagicMock(return_value="b2b/req-1.pdf")
    db.storage.from_.return_value.get_s3_key.return_value = "s3://reports/b2b/req-1.pdf"
    svc.deliver_request_pdf = MagicMock(return_value=["client@studio.com"])

    svc.process_request("req-1")

    # Sufficient data => a report is generated and completed, never held.
    svc.pdf_service.generate_pdf_bytes.assert_called_once()
    svc.deliver_request_pdf.assert_called_once()
    statuses = [p.get("status") for p in _update_payloads(db)]
    assert "completed" in statuses and "held" not in statuses
    assert "b2b_intelligence_held" not in _sent_templates(svc.email_service)


def test_admin_alert_falls_back_to_contact_email():
    """With no dedicated B2B_ADMIN_ALERT_EMAIL, the ops alert uses CONTACT_EMAIL."""
    svc, db = _service()
    svc.get_request = MagicMock(return_value=_request_row())
    svc.build_period_metrics = MagicMock(return_value={
        "insufficient_data": True, "source_signal_count": 1,
        "thresholds": {"minimum_overall_records": 10}, "title": "X",
    })

    svc.process_request("req-1")

    alert_calls = [c for c in svc.email_service.send.call_args_list if c.args[1] == "b2b_admin_alert"]
    assert alert_calls and alert_calls[0].args[0] == "ops@example.com"
