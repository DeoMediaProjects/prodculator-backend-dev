"""Tests for signal-pool visibility and governance controls (SOW 4.5).

The load-bearing rule: an admin may REVOKE b2b_consent but never GRANT it.
Granting would manufacture consent on a producer's behalf and pull their
production into commercial reports they never agreed to, which is the exact
failure the consent flag exists to prevent.
"""
from datetime import date
from unittest.mock import MagicMock

import pytest

from app.modules.b2b.composer_service import ConsentGrantRefused, SignalPoolService


def _row(sid, *, consent=True, internal=False, submitted="2026-05-10"):
    return {
        "id": sid,
        "script_id": f"script-{sid}",
        "submission_date": submitted,
        "territory": "United Kingdom",
        "home_country": "United Kingdom",
        "format": "Feature Film",
        "b2b_consent": consent,
        "is_internal": internal,
    }


def _service(rows):
    db = MagicMock()
    chain = db.table.return_value.select.return_value
    # The service may or may not apply date filters, so both the filtered and
    # unfiltered ends of the chain resolve to the same rows.
    chain.execute.return_value.data = rows
    chain.gte.return_value.execute.return_value.data = rows
    chain.gte.return_value.lte.return_value.execute.return_value.data = rows
    chain.lte.return_value.execute.return_value.data = rows
    chain.eq.return_value.execute.return_value.data = rows
    return SignalPoolService(db), db


class TestSummary:
    def test_eligible_excludes_unconsented_and_internal(self):
        rows = [
            _row("a"),                          # eligible
            _row("b"),                          # eligible
            _row("c", consent=False),           # excluded: no consent
            _row("d", internal=True),           # excluded: internal
            _row("e", consent=False, internal=True),  # excluded: both
        ]
        svc, _ = _service(rows)
        s = svc.summary()

        assert s["total"] == 5
        assert s["eligible"] == 2
        assert s["excluded"] == 3
        assert s["consented"] == 3
        assert s["not_consented"] == 2
        assert s["internal"] == 2

    def test_excluded_reasons_do_not_double_count(self):
        """A row that is both unconsented AND internal is attributed to the
        consent bucket only, so the reason counts stay interpretable."""
        rows = [_row("a"), _row("b", consent=False, internal=True)]
        svc, _ = _service(rows)
        reasons = svc.summary()["excluded_reasons"]

        assert reasons["no_consent"] == 1
        # 'internal' counts only rows that WOULD have been eligible but for the flag.
        assert reasons["internal"] == 0

    def test_empty_pool(self):
        svc, _ = _service([])
        s = svc.summary()
        assert s["total"] == 0 and s["eligible"] == 0 and s["excluded"] == 0

    def test_eligibility_mirrors_load_signals(self):
        """Eligibility here must match B2BService._load_signals exactly, or the
        pool view would promise data the aggregates do not actually use."""
        assert SignalPoolService._is_eligible({"b2b_consent": True, "is_internal": False})
        assert not SignalPoolService._is_eligible({"b2b_consent": False, "is_internal": False})
        assert not SignalPoolService._is_eligible({"b2b_consent": True, "is_internal": True})
        # Missing keys are falsy, so an un-migrated row is excluded rather than leaked.
        assert not SignalPoolService._is_eligible({})


class TestListSignals:
    def test_filters_by_consent_and_internal(self):
        rows = [_row("a"), _row("b", consent=False), _row("c", internal=True)]
        svc, _ = _service(rows)

        assert svc.list_signals(consent=True)["total"] == 2
        assert svc.list_signals(consent=False)["total"] == 1
        assert svc.list_signals(internal=True)["total"] == 1
        assert svc.list_signals(consent=True, internal=False)["total"] == 1

    def test_pagination(self):
        svc, _ = _service([_row(str(i)) for i in range(10)])
        page = svc.list_signals(limit=3, offset=6)
        assert page["total"] == 10
        assert len(page["items"]) == 3
        assert page["offset"] == 6

    def test_items_expose_flags_and_eligibility(self):
        svc, _ = _service([_row("a", internal=True)])
        item = svc.list_signals()["items"][0]
        assert item["b2b_consent"] is True
        assert item["is_internal"] is True
        assert item["eligible"] is False

    def test_item_payload_is_narrow(self):
        """The pool view is a governance tool, not a data browser: it must not
        leak budget, crew, cast or audience detail about a production."""
        svc, _ = _service([_row("a")])
        item = svc.list_signals()["items"][0]
        leaky = {
            "budget_amount_gbp", "budget_range", "crew_size", "principal_cast",
            "supporting_cast", "background_extras", "genres", "target_audience",
            "audience_segments", "territories_recommended",
        }
        assert not (leaky & set(item)), f"pool view leaks: {leaky & set(item)}"

    def test_sorted_newest_first(self):
        rows = [
            _row("old", submitted="2026-01-01"),
            _row("new", submitted="2026-06-01"),
            _row("mid", submitted="2026-03-01"),
        ]
        svc, _ = _service(rows)
        ids = [i["id"] for i in svc.list_signals()["items"]]
        assert ids == ["new", "mid", "old"]


class TestConsentIsRevokeOnly:
    def test_granting_consent_is_refused(self):
        svc, db = _service([_row("a", consent=False)])
        with pytest.raises(ConsentGrantRefused):
            svc.set_consent("a", True)
        # Nothing was written.
        db.table.return_value.update.assert_not_called()

    def test_granting_is_refused_before_the_row_is_even_looked_up(self):
        """The refusal is unconditional, so it cannot be probed for row existence."""
        svc, _ = _service([])
        with pytest.raises(ConsentGrantRefused):
            svc.set_consent("does-not-exist", True)

    def test_revoking_consent_writes_false(self):
        svc, db = _service([_row("a", consent=True)])
        svc.set_consent("a", False)
        payload = db.table.return_value.update.call_args[0][0]
        assert payload["b2b_consent"] is False

    def test_revoke_returns_none_for_missing_signal(self):
        svc, _ = _service([])
        assert svc.set_consent("nope", False) is None


class TestInternalFlagIsSymmetric:
    def test_can_mark_internal(self):
        svc, db = _service([_row("a")])
        svc.set_internal("a", True)
        assert db.table.return_value.update.call_args[0][0]["is_internal"] is True

    def test_can_restore_to_customer_facing_pool(self):
        svc, db = _service([_row("a", internal=True)])
        svc.set_internal("a", False)
        assert db.table.return_value.update.call_args[0][0]["is_internal"] is False

    def test_returns_none_for_missing_signal(self):
        svc, _ = _service([])
        assert svc.set_internal("nope", True) is None
