"""The generated territory reference must stay accurate and safe to circulate.

Three properties are load bearing and none of them is visible by eye in a
300 kilobyte document:

  * No em dash reaches the output. The house style forbids it, and the source
    data is full of them.
  * ``internal_audit_notes`` never appears. It carries data team annotations
    that must not reach a client facing surface (PROD-FIX-006), and this
    document goes to external financial reviewers.
  * Driver types are normalised before the engine runs. A boolean handed back as
    the string "False" is truthy, which makes every programme look supplementary,
    finds no alternative when a budget exceeds an eligibility ceiling, and prints
    a capped out rate as though it applied. That is a plausible wrong number
    rather than a crash.
"""
from __future__ import annotations

import re

import pytest
import sqlalchemy as sa

from scripts.generate_incentive_reference import (
    EXCLUDED_COLUMNS,
    build_html,
    coerce_row,
)
from app.modules.reports.pdf_service import strip_em_dashes


def _row(**overrides) -> dict:
    row = {
        "territory": "United Kingdom",
        "program": "AVEC (Enhanced/IFTC)",
        "status": "active",
        "is_supplementary": False,
        "rate": "53%",
        "rate_gross": 53.0,
        "rate_net": 39.75,
        "rate_type": "tax_credit",
        "rate_tier_json": None,
        "atl_exempt": True,
        "qualifying_spend_type": "total",
        "qualifying_spend_cap_pct": 80.0,
        "qualifying_spend_cap_amount": 12_000_000.0,
        "qualifying_spend_cap_currency": "GBP",
        "qualifying_spend_labour_pct": None,
        "cap_amount": 23_500_000.0,
        "cap_currency": "GBP",
        "cap_per_person": None,
        "rebate_cap_amount": 6_360_000.0,
        "rebate_cap_currency": "GBP",
        "currency": "GBP",
        "last_verified_at": "2026-08-19",
        "source_url": "https://www.gov.uk/guidance/audio-visual-expenditure-credit",
        # An em dash and an audit annotation, both of which must not survive.
        "notes": "Relief is limited to GBP 15M core costs — above that, AVEC applies.",
        "internal_audit_notes": "[FLAGGED 2026-07: needs a confirmation call]",
        "payment_timeline_notes": None,
    }
    row.update(overrides)
    return row


def _avec() -> dict:
    return _row(
        program="UK Audio-Visual Expenditure Credit (AVEC)",
        rate="34%", rate_gross=34.0, rate_net=25.5,
        qualifying_spend_cap_amount=None, qualifying_spend_cap_currency=None,
        cap_amount=None, rebate_cap_amount=None, rebate_cap_currency=None,
        notes="Standard programme.", internal_audit_notes=None,
    )


def _render(rows, other_rows=()):
    columns = sorted({k for r in list(rows) + list(other_rows) for k in r}
                     - EXCLUDED_COLUMNS)
    stripped = [
        {k: v for k, v in r.items() if k not in EXCLUDED_COLUMNS}
        for r in rows
    ]
    stripped_other = [
        {k: v for k, v in r.items() if k not in EXCLUDED_COLUMNS}
        for r in other_rows
    ]
    return strip_em_dashes(
        build_html(stripped, stripped_other, columns, "test fixture")
    )


# ── house style ──────────────────────────────────────────────────────────────


class TestHouseStyle:
    def test_no_em_dash_survives_anywhere(self):
        html = _render([_row(), _avec()])
        assert "—" not in html
        assert "&mdash;" not in html

    def test_the_source_em_dash_became_a_comma_rather_than_vanishing(self):
        """The clause must still read, not lose its separator silently."""
        html = _render([_row()])
        assert "GBP 15M core costs, above that" in html

    def test_headings_carry_no_em_dash(self):
        html = _render([_row(), _avec()])
        for heading in re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", html, re.S):
            assert "—" not in heading


# ── confidentiality ──────────────────────────────────────────────────────────


class TestConfidentiality:
    def test_internal_audit_notes_is_excluded_by_name(self):
        assert "internal_audit_notes" in EXCLUDED_COLUMNS

    def test_no_audit_annotation_reaches_the_document(self):
        html = _render([_row()])
        assert "[FLAGGED" not in html
        assert "confirmation call" not in html

    def test_the_watermark_is_present(self):
        html = _render([_row()])
        assert "Property of DeoMedia and Prodculator" in html

    def test_the_logo_is_embedded_not_linked(self):
        """A strict-CSP viewer or an offline PDF must still show the mark."""
        html = _render([_row()])
        assert "data:image/jpeg;base64," in html
        assert "<img src=\"http" not in html


# ── type safety ──────────────────────────────────────────────────────────────


class TestCoercion:
    @pytest.mark.parametrize("raw,expected", [("False", False), ("false", False),
                                              ("0", False), ("", False),
                                              ("True", True), ("t", True), ("1", True)])
    def test_boolean_strings_are_normalised(self, raw, expected):
        assert coerce_row({"is_supplementary": raw})["is_supplementary"] is expected

    @pytest.mark.parametrize("raw,expected", [("23500000.0", 23_500_000.0),
                                              ("23,500,000", 23_500_000.0),
                                              (" 80 ", 80.0), ("", None),
                                              ("not a number", None)])
    def test_numeric_strings_are_normalised(self, raw, expected):
        assert coerce_row({"cap_amount": raw})["cap_amount"] == expected

    def test_real_types_pass_through_untouched(self):
        row = {"cap_amount": 23_500_000.0, "is_supplementary": True, "program": "x"}
        assert coerce_row(row) == row

    def test_a_stringly_typed_row_still_switches_programme(self):
        """The bug this guards: with is_supplementary as the string "False",
        every programme looks supplementary, so no alternative is found and a
        GBP 40M budget keeps the IFTC rate it is not eligible for."""
        iftc = {k: (str(v) if isinstance(v, (bool, float)) and v is not None else v)
                for k, v in _row().items()}
        avec = {k: (str(v) if isinstance(v, (bool, float)) and v is not None else v)
                for k, v in _avec().items()}
        html = _render([coerce_row(iftc), coerce_row(avec)])
        # At GBP 40M the IFTC block must show the switch, not a 53 percent rate.
        block = re.search(
            r"<h3>AVEC \(Enhanced/IFTC\)</h3>.*?class=\"grid calc\".*?</table>",
            html, re.S,
        )
        assert block, "IFTC worked calculation missing"
        rows = re.findall(r"<tr>(.*?)</tr>", block.group(0), re.S)
        forty = next(r for r in rows if "40,000,000" in r)
        assert "Budget exceeds" in forty, "programme switch did not fire"
        assert "34.00% gross" in forty


# ── completeness ─────────────────────────────────────────────────────────────


class TestCompleteness:
    def test_non_claimable_records_are_listed_not_dropped(self):
        suspended = _row(
            territory="Western Cape", program="Provincial rebate",
            status="suspended", notes="Programme suspended.",
            internal_audit_notes=None,
        )
        html = _render([_row()], other_rows=[suspended])
        assert "no claimable programme" in html
        assert "Western Cape" in html
        assert "suspended" in html

    def test_worked_examples_are_computed_for_every_programme(self):
        html = _render([_row(), _avec()])
        assert len(re.findall(r'class="grid calc"', html)) == 2

    def test_a_programme_with_no_computable_figure_says_why(self):
        pdv = _row(
            program="UK VFX Expenditure Credit (Uplift)",
            qualifying_spend_type="pdv", qualifying_spend_labour_pct=None,
            rebate_cap_amount=None, cap_amount=None, internal_audit_notes=None,
        )
        html = _render([pdv])
        assert "no sourced share" in html
        assert "without a figure rather than with an estimated one" in html

    def test_every_populated_column_appears_somewhere(self):
        """A schema addition must not silently vanish from the document."""
        row = _row(some_new_column="a value the generator has never heard of")
        html = _render([row])
        assert "some_new_column" in html
        assert "a value the generator has never heard of" in html


# ── the numbers themselves ───────────────────────────────────────────────────


def test_the_uk_figures_match_hmrc():
    """GBP 20M budget, IFTC: qualifying spend capped at the fixed GBP 12M
    ceiling, gross GBP 6.36M, net GBP 4.77M. Published maximum credit is
    GBP 6.36M, being GBP 15M x 80 percent x 53 percent."""
    html = _render([_row(), _avec()])
    block = re.search(
        r"<h3>AVEC \(Enhanced/IFTC\)</h3>.*?class=\"grid calc\".*?</table>",
        html, re.S,
    ).group(0)
    twenty = next(r for r in re.findall(r"<tr>(.*?)</tr>", block, re.S)
                  if "20,000,000" in r)
    assert "12,000,000" in twenty
    assert "6,360,000" in twenty
    assert "4,770,000" in twenty


def test_the_document_renders_against_a_real_database(tmp_path):
    """End to end through SQLAlchemy, the way the script actually runs."""
    db = tmp_path / "ref.db"
    engine = sa.create_engine(f"sqlite:///{db}")
    rows = [_row(), _avec()]
    columns = sorted({k for r in rows for k in r})
    metadata = sa.MetaData()
    table = sa.Table("incentive_programs", metadata,
                     *[sa.Column(c, sa.Text()) for c in columns])
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(table.insert(), [
            {k: (None if v is None else str(v)) for k, v in r.items()} for r in rows
        ])

    with engine.connect() as conn:
        selectable = [c for c in columns if c not in EXCLUDED_COLUMNS]
        query = sa.text(
            f'SELECT {", ".join(chr(34) + c + chr(34) for c in selectable)} '
            f"FROM incentive_programs ORDER BY territory, program"
        )
        loaded = [coerce_row(dict(r)) for r in conn.execute(query).mappings()]

    html = strip_em_dashes(build_html(loaded, [], selectable, "sqlite fixture"))
    assert "—" not in html
    assert "[FLAGGED" not in html
    assert len(html) > 5_000
