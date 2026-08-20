"""The v4 ingest must not discard a cap it cannot reduce to a bare number.

``_money()`` anchors at the start of the string and ``_first_number()`` keeps only
the leading figure, so every cap stated with a qualification around it was lost in
silence — unlike ``qsMin``, which has always fallen back to a warning. Four rows
reached the engine as uncapped programmes and one lost the ceiling that mattered
most (UK IFTC's GBP 12,000,000 qualifying-expenditure limit).

These tests pin the parsers against the real v4 source strings.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_V4_REFRESH = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "ab2c3d4e5f61_incentives_v4_refresh.py"
)


@pytest.fixture(scope="module")
def v4():
    """The refresh migration loaded as a plain module (alembic.op stubbed out)."""
    saved = sys.modules.get("alembic")
    stub = types.ModuleType("alembic")
    stub.op = types.SimpleNamespace(add_column=lambda *a, **k: None)
    sys.modules["alembic"] = stub
    try:
        spec = importlib.util.spec_from_file_location("_v4_cap_ingest", _V4_REFRESH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        if saved is not None:
            sys.modules["alembic"] = saved
        else:
            sys.modules.pop("alembic", None)


# ── The compound qualifying-spend cap ────────────────────────────────────────


class TestQualifyingSpendCapParsing:
    _IFTC = (
        "80% of core expenditure, capped at GBP 12,000,000 qualifying "
        "expenditure regardless of budget size within eligibility"
    )

    def test_both_halves_are_kept(self, v4):
        pct, amount, currency = v4._qualifying_spend_cap(self._IFTC)
        assert pct == 80.0
        assert amount == 12_000_000.0
        assert currency == "GBP"

    def test_a_bare_percentage_yields_no_false_ceiling(self, v4):
        pct, amount, currency = v4._qualifying_spend_cap("80%")
        assert pct == 80.0
        assert amount is None
        assert currency is None

    def test_a_qualified_percentage_yields_no_false_ceiling(self, v4):
        """BC PSTC: "100% of BC labour expenditure" states a basis, not a cap."""
        pct, amount, _ = v4._qualifying_spend_cap("100% of BC labour expenditure")
        assert pct == 100.0
        assert amount is None

    @pytest.mark.parametrize("value", [None, "None", "N/A"])
    def test_absent_values_parse_to_nothing(self, v4, value):
        assert v4._qualifying_spend_cap(value) == (None, None, None)

    def test_the_live_iftc_row_carries_the_ceiling(self, v4):
        """End-to-end through _build_row, on the actual source row."""
        row = next(
            r for r in v4._SOURCE_ROWS
            if r["terr"] == "United Kingdom" and "IFTC" in r["prog"]
        )
        built = v4._build_row(row, "2026-01-01")
        assert built["qualifying_spend_cap_pct"] == 80.0
        assert built["qualifying_spend_cap_amount"] == 12_000_000.0
        assert built["qualifying_spend_cap_currency"] == "GBP"
        # The gross ceiling must survive alongside it.
        assert built["rebate_cap_amount"] == 6_360_000.0


# ── Rebate ceilings stated with a qualification ───────────────────────────────


class TestEmbeddedRebateCapParsing:
    @pytest.mark.parametrize(
        "territory,name_fragment,expected_amount,expected_currency",
        [
            ("Belgium", "Tax Shelter", 7_250_000.0, "EUR"),
            ("Netherlands", "Film Production Incentive", 3_000_000.0, "EUR"),
            ("Mexico", "EFICA", 40_000_000.0, "MXN"),
        ],
    )
    def test_previously_dropped_caps_are_now_stored(
        self, v4, territory, name_fragment, expected_amount, expected_currency
    ):
        row = next(
            r for r in v4._SOURCE_ROWS
            if r["terr"] == territory and name_fragment in r["prog"]
        )
        built = v4._build_row(row, "2026-01-01")
        assert built["rebate_cap_amount"] == expected_amount
        assert built["rebate_cap_currency"] == expected_currency

    def test_the_leading_per_project_figure_wins_over_the_programme_pool(self, v4):
        """Mexico states both a per-project and a programme-wide limit. Only the
        per-project figure is a rebate ceiling; the pool is an annual allocation."""
        parsed = v4._embedded_money(
            "MXN 40,000,000 per production/beneficiary; "
            "MXN 400,000,000 total programme limit"
        )
        assert parsed == ("MXN", 40_000_000.0)

    def test_an_absence_assertion_does_not_become_a_warning(self, v4):
        """"No cap" is information, not a dropped constraint — no noise for it."""
        row = next(
            r for r in v4._SOURCE_ROWS
            if r["terr"] == "United Kingdom" and "Audio-Visual" in r["prog"]
        )
        built = v4._build_row(row, "2026-01-01")
        assert built["rebate_cap_amount"] is None
        warnings = built["warnings_json"] or ""
        assert "Rebate cap:" not in warnings

    def test_an_unparsable_ceiling_survives_as_prose(self, v4):
        """Portugal's cap is described, not quantified. It must still be said."""
        built = v4._build_row(
            {
                "terr": "United Kingdom",
                "prog": "Synthetic row",
                "capType": "output",
                "rebateCap": "Broadly limited to the annual pool, amount unpublished",
            },
            "2026-01-01",
        )
        assert built["rebate_cap_amount"] is None
        assert "Rebate cap:" in (built["warnings_json"] or "")


# ── No row silently loses a stated cap ───────────────────────────────────────


def test_no_source_row_drops_a_stated_output_cap(v4):
    """Sweep every row: an output cap that is neither parsed nor an explicit
    absence must appear in warnings. This is the invariant the original ingest
    broke for four territories at once."""
    offenders = []
    for src in v4._SOURCE_ROWS:
        if src.get("capType") != "output":
            continue
        raw = src.get("rebateCap")
        if v4._is_none_str(raw) or v4._asserts_no_cap(raw):
            continue
        built = v4._build_row(src, "2026-01-01")
        if built["rebate_cap_amount"] is None and "Rebate cap:" not in (
            built["warnings_json"] or ""
        ):
            offenders.append(f"{src['terr']} — {src['prog']}: {raw!r}")
    assert not offenders, "cap constraints dropped in silence:\n" + "\n".join(offenders)
