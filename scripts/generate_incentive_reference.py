"""Generate the Territory Incentive and Calculation Reference document.

Every figure in the output comes from one of two places and neither is authored
by hand:

  * Programme data is read from ``incentive_programs`` in the database this is
    pointed at. Nothing is reconstructed from the migration history, because 66
    migrations patch that table after the v4 refresh and replaying them is not
    trustworthy. The live table is the only authority.

  * Worked examples are produced by calling the production engine,
    ``ReportValidator._compute_corrected_rebate``, on the real rows. The numbers
    printed are the numbers a report would print, including the cases where the
    engine declines to produce a figure.

Usage (run where DB_URL resolves):

    DB_URL=<connection string> python -m scripts.generate_incentive_reference

Writes ``incentive_reference.html`` next to the repo root by default, and also a
PDF when WeasyPrint's native libraries are available (they are in the Docker
image, they are not on Windows). Print the HTML to PDF from a browser otherwise.

``internal_audit_notes`` is deliberately excluded: it carries data-team
annotations that must never reach a client-facing surface (PROD-FIX-006). Every
other populated column is shown.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import sqlalchemy as sa

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from app.modules.reports.helpers import (  # noqa: E402
    DEFAULT_ATL_PCT,
    STALE_DAYS,
    TAX_CREDIT_RATE_TYPES,
    mechanism_no_figure_reason,
)
from app.modules.reports.pdf_service import strip_em_dashes  # noqa: E402
from app.modules.reports.validator import ReportValidator  # noqa: E402

# Budgets the worked examples are computed at, in GBP. Chosen to sit either side
# of the thresholds that actually exist in the dataset: the UK IFTC reference
# amount (GBP 15M qualifying ceiling) and its GBP 23.5M eligibility ceiling.
EXAMPLE_BUDGETS_GBP = (5_000_000.0, 20_000_000.0, 40_000_000.0)

#: Admin-only column. See module docstring.
EXCLUDED_COLUMNS = frozenset({"internal_audit_notes"})

#: Presentation grouping. Any column not listed here still appears, under
#: "Other recorded fields", so a schema change cannot hide data from this
#: document.
FIELD_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Identity and status", (
        "territory", "program", "region", "parent_territory", "scope",
        "programme_level", "status", "is_supplementary", "expiry_date",
    )),
    ("Headline rate", (
        "rate", "rate_gross", "rate_net", "net_rate_pct", "rate_type",
        "rate_gross_display", "rate_net_display", "rate_tier_json",
        "vfx_uplift_pct", "currency",
    )),
    ("Qualifying spend basis", (
        "qualifying_spend_type", "qualifying_spend_cap_pct",
        "qualifying_spend_cap_amount", "qualifying_spend_cap_currency",
        "qualifying_spend_labour_pct", "qualifying_spend_min",
        "qualifying_spend_currency", "qs_basis", "calc_formula", "atl_exempt",
    )),
    ("Caps and ceilings", (
        "cap", "cap_type", "cap_amount", "cap_currency", "cap_basis",
        "rebate_cap_amount", "rebate_cap_currency", "rebate_cap_display",
        "cap_per_person", "cap_per_person_currency", "per_person_cap_display",
        "budget_eligibility_ceiling", "annual_programme_cap",
    )),
    ("Eligibility", (
        "applicable_formats", "cultural_test_required",
        "nationality_requirements", "co_production_eligible",
        "co_production_treaties", "spv_eligible", "eligibility_rules_json",
        "eligibility_notes", "admin_complexity",
    )),
    ("Stacking", ("stackable_with", "stacking_group", "mechanism_pattern")),
    ("Payment and reliability", (
        "payment_timeline", "payment_timeline_days_min",
        "payment_timeline_days_max", "payment_timeline_notes",
        "payment_reliability", "bank_pts", "payee_note", "filing_note",
    )),
    ("Provenance", (
        "authority", "source_name", "source_url", "verification_status",
        "last_verified_at", "last_updated", "confidence", "auto_sync_enabled",
        "last_auto_check",
    )),
    ("Notes and warnings", ("notes", "warnings_json", "regional_funds_note", "ai_rule")),
)

_HIDE_ALWAYS = frozenset({"id", "created_at", "updated_at"})

FIELD_GLOSSARY: tuple[tuple[str, str], ...] = (
    ("rate_gross", "Headline credit or rebate rate before the gross to net haircut. Not the investor facing figure."),
    ("rate_net", "Net cash value of the credit after corporation tax effects. This is the figure quoted to investors."),
    ("rate_type", "Mechanism. Drives whether an above the line deduction applies. Tax credit types are "
                  + ", ".join(sorted(TAX_CREDIT_RATE_TYPES)) + "."),
    ("rate_tier_json", "Banded rates. tier_type 'spend_boundary' blends two rates across a parsed boundary. "
                       "tier_type 'informational' describes categories and does not blend."),
    ("qualifying_spend_type", "What the rate applies to. 'total' and 'local_spend' use the budget subject to the caps below. "
                              "'labour' and 'pdv' require a sourced share and produce no figure without one."),
    ("qualifying_spend_cap_pct", "Proportional restriction on qualifying spend, for example 80 percent of core expenditure."),
    ("qualifying_spend_cap_amount", "Absolute ceiling on qualifying spend that does not rise with the budget."),
    ("qualifying_spend_labour_pct", "Sourced labour or post production share. Required for 'labour' and 'pdv' bases."),
    ("cap_amount", "Budget eligibility threshold. Exceeding it moves the calculation to an alternative programme in the same territory."),
    ("cap_basis", "What cap_amount is measured against. 'core_costs' means core production costs rather than total budget."),
    ("rebate_cap_amount", "Maximum gross credit per project. Applied after the rate, and distinct from cap_amount."),
    ("cap_per_person", "Per individual wage or fee ceiling. Surfaced as a risk note rather than modelled per head."),
    ("atl_exempt", "True where the programme makes no above the line distinction, so no ATL deduction is applied."),
    ("payment_reliability", "Curated zero to one confidence that the programme pays as documented."),
    ("last_verified_at", f"Date the record was last checked against its source. Records older than {STALE_DAYS} days are flagged stale in reports."),
    ("is_supplementary", "Programme applies to a subset of spend and is never selected as a territory's primary incentive."),
)


# ── formatting helpers ───────────────────────────────────────────────────────


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False


#: Columns the engine reads as numbers or booleans. A driver that hands these
#: back as strings silently changes the result rather than raising: the engine
#: tests `not row["is_supplementary"]`, and the string "False" is truthy, so
#: every programme looks supplementary, no alternative is found, and a budget
#: over a programme's eligibility ceiling keeps the capped-out rate. That
#: produces a plausible wrong number in a document a financial reader will
#: rely on, so the types are normalised before the engine sees the row.
_NUMERIC_COLUMNS = frozenset({
    "rate_gross", "rate_net", "net_rate_pct", "cap_amount", "cap_per_person",
    "rebate_cap_amount", "qualifying_spend_min", "qualifying_spend_cap_pct",
    "qualifying_spend_cap_amount", "qualifying_spend_labour_pct",
    "payment_reliability", "vfx_uplift_pct", "confidence", "bank_pts",
    "payment_timeline_days_min", "payment_timeline_days_max",
})
_BOOLEAN_COLUMNS = frozenset({
    "is_supplementary", "atl_exempt", "cultural_test_required",
    "co_production_eligible", "spv_eligible", "auto_sync_enabled",
})
_FALSEY_STRINGS = frozenset({"", "false", "f", "0", "no", "n", "none", "null"})


def coerce_row(row: dict) -> dict:
    """Normalise driver-dependent types so the engine behaves as it does in the app."""
    out = dict(row)
    for column in _NUMERIC_COLUMNS & out.keys():
        value = out[column]
        if isinstance(value, str):
            try:
                out[column] = float(value.replace(",", "").strip()) if value.strip() else None
            except ValueError:
                out[column] = None
    for column in _BOOLEAN_COLUMNS & out.keys():
        value = out[column]
        if isinstance(value, str):
            out[column] = value.strip().lower() not in _FALSEY_STRINGS
    return out


def money(amount: float | None, currency: str = "GBP") -> str:
    if amount is None:
        return "not applicable"
    symbols = {"GBP": "£", "EUR": "€", "USD": "$"}
    symbol = symbols.get((currency or "GBP").upper(), (currency or "") + " ")
    return f"{symbol}{amount:,.0f}"


def pretty_value(column: str, value: Any) -> str:
    """Render one cell. JSON columns are unpacked so a reviewer can read them."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if column.endswith("_json") or column in {"applicable_formats", "stackable_with",
                                              "co_production_treaties",
                                              "nationality_requirements"}:
        parsed = value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (ValueError, TypeError):
                return esc(value)
        if isinstance(parsed, list):
            if not parsed:
                return "none recorded"
            items = []
            for entry in parsed:
                if isinstance(entry, dict):
                    items.append(", ".join(f"{k}: {v}" for k, v in entry.items()))
                else:
                    items.append(str(entry))
            return "<ul class='mini'>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>"
        return esc(parsed)
    if isinstance(value, float) and value.is_integer():
        return esc(int(value))
    return esc(value)


# ── document sections ────────────────────────────────────────────────────────


def methodology_html() -> str:
    """The calculation contract, written from the engine rather than from memory.

    Every constant quoted here is imported at the top of this module, so the
    document cannot drift from the code.
    """
    atl_pct = f"{DEFAULT_ATL_PCT:.0%}"
    fx = ReportValidator._REBATE_CAP_STATIC_FX
    fx_rows = "".join(
        f"<tr><td>{esc(ccy)}</td><td class='num'>{rate:g}</td></tr>"
        for ccy, rate in sorted(fx.items())
    )
    tax_types = ", ".join(sorted(TAX_CREDIT_RATE_TYPES))

    return f"""
<section class="page">
  <h1>How the calculation works</h1>

  <p class="lede">A rebate figure is produced by one function,
  <code>ReportValidator._compute_corrected_rebate</code>. It runs in five ordered
  steps. The order matters: every step consumes the output of the one before it,
  so a cap applied at the wrong point changes the result rather than merely the
  wording.</p>

  <h2>Step 1. Establish qualifying spend</h2>
  <p>The rate is never applied to the total budget by default. What it applies to
  is set by <code>qualifying_spend_type</code>:</p>
  <table class="grid">
    <thead><tr><th>Basis</th><th>Qualifying spend</th><th>Behaviour when data is absent</th></tr></thead>
    <tbody>
      <tr><td>total</td><td>Budget, then the caps below</td><td>Full budget used</td></tr>
      <tr><td>local_spend</td><td>In territory spend, assumed to be the full qualifying budget</td><td>Full budget used, with a stated assumption</td></tr>
      <tr><td>labour</td><td>Budget multiplied by <code>qualifying_spend_labour_pct</code></td><td>No figure is produced at all</td></tr>
      <tr><td>pdv</td><td>Budget multiplied by <code>qualifying_spend_labour_pct</code></td><td>No figure is produced at all</td></tr>
    </tbody>
  </table>
  <p>The behaviour in the final column is deliberate. A labour only credit needs a
  sourced labour share to produce an honest number, and the engine presents the
  programme without a computed rebate rather than inventing a ratio.</p>

  <p>Two caps then apply to the <code>total</code> and <code>local_spend</code>
  bases, in this order:</p>
  <ol>
    <li><strong>Proportional cap.</strong> <code>qualifying_spend_cap_pct</code>,
    where set and below 100, multiplies the budget. A value of 80 means 80 percent
    of core expenditure qualifies.</li>
    <li><strong>Absolute ceiling.</strong> <code>qualifying_spend_cap_amount</code>,
    where set, caps the result outright. This ceiling does not rise with the
    budget, so above a certain budget the qualifying spend stops growing.</li>
  </ol>
  <p>The combined effect is
  <code>qualifying_spend = MIN(pct &times; budget, absolute ceiling)</code>.
  Both are stated in the report rather than applied silently.</p>

  <h2>Step 2. Programme selection and rate tiers</h2>
  <p>If the budget exceeds <code>cap_amount</code>, the programme may not apply.
  The engine looks for an alternative in the same territory that is not
  supplementary, does not have a zero rate, and is not itself capped below this
  budget. If one exists the calculation switches to it, and every attribute from
  that point on is read from the replacement programme, including its own
  qualifying spend basis. Carrying the original programme's basis across a switch
  would model a rate against the wrong denominator.</p>
  <p>Where <code>cap_basis</code> is <code>core_costs</code>, the threshold is
  measured against core production costs rather than total budget. The engine
  still switches, conservatively, and adds a note that the original programme may
  still apply if core costs fall below the threshold.</p>
  <p>Rate tiers are read from <code>rate_tier_json</code>. A
  <code>spend_boundary</code> tier set blends two rates across the boundary parsed
  from the tier label, weighted by the spend falling either side. An
  <code>informational</code> tier set describes categories and is not blended, so
  the headline rate is used. A missing <code>tier_type</code> is treated as
  informational, which avoids fabricating a blended rate from an unclassified
  label.</p>

  <h2>Step 3. Above the line deduction</h2>
  <p>Tax credit programmes typically exclude above the line costs, being writer,
  director and lead cast fees, from qualifying spend. The engine deducts an
  estimated <strong>{atl_pct} of total budget</strong> when all of the following
  hold:</p>
  <ul>
    <li><code>rate_type</code> is one of {tax_types}</li>
    <li><code>atl_exempt</code> is not true</li>
    <li><code>qualifying_spend_type</code> is not labour or pdv</li>
  </ul>
  <p>The last two conditions prevent double discounting. A programme with no
  statutory above the line distinction is exempt, and a labour or post production
  share is already a below the line weighted estimate.</p>
  <p>Where <code>cap_per_person</code> is set, it is surfaced as a risk note
  rather than modelled per individual, since the engine does not hold a cast and
  crew fee breakdown. On productions with expensive talent this materially reduces
  the real credit, and the note says so.</p>

  <h2>Step 4. Apply the rate</h2>
  <p>Gross and net are computed independently from the qualifying spend
  established above:</p>
  <div class="formula">
    gross_rebate = qualifying_spend &times; rate_gross &divide; 100<br>
    net_rebate = qualifying_spend &times; rate_net &divide; 100
  </div>
  <p>Where <code>rate_net</code> is absent the gross figure is used for both. The
  net figure is the one quoted to investors.</p>

  <h2>Step 5. Enforce the per project rebate ceiling</h2>
  <p><code>rebate_cap_amount</code> is the maximum gross credit a project can
  receive, and is distinct from <code>cap_amount</code>, which is a budget
  threshold. Where the computed gross exceeds it, the gross is reduced to the
  ceiling and the net is scaled by the programme's own rate ratio:</p>
  <div class="formula">
    net_rebate = ceiling &times; (rate_net &divide; rate_gross)
  </div>
  <p>The ceiling constrains the gross credit, so the net cannot simply be set to
  the same number. On a 53 percent gross and 39.75 percent net programme, a
  ceiling of {money(6_360_000)} carries a net value of
  {money(6_360_000 * 39.75 / 53)}.</p>

  <p>Where the ceiling is denominated in another currency it is converted using
  live foreign exchange when available. Failing that, a static fallback table is
  used, which errs toward a smaller sterling ceiling:</p>
  <table class="grid narrow">
    <thead><tr><th>Currency</th><th>Units per GBP</th></tr></thead>
    <tbody>{fx_rows}</tbody>
  </table>

  <h2>Eligibility is resolved separately, and it can override the figure</h2>
  <p>A number is only presented as realisable if the project clears every
  eligibility dimension. Four checks are combined once, with a fixed precedence,
  and every section of a report reads that single result:</p>
  <table class="grid">
    <thead><tr><th>Outcome</th><th>Condition</th><th>Effect on the figure</th></tr></thead>
    <tbody>
      <tr><td>Ineligible</td><td>Any hard failure, being budget below the programme minimum, format excluded, or producer structurally excluded with no route in</td><td>No figure is shown</td></tr>
      <tr><td>Unverified</td><td>A required dimension is unknown</td><td>Figure shown as illustrative only</td></tr>
      <tr><td>Conditional</td><td>A stated route exists but has not been built</td><td>Figure shown as illustrative only</td></tr>
      <tr><td>Eligible</td><td>All required dimensions pass</td><td>Figure may affect net cost, ranking and recommendations</td></tr>
    </tbody>
  </table>
  <p>A status is only as strong as its weakest required dimension. A structurally
  qualified producer cannot outvote an unknown format.</p>

  <h2>Data freshness</h2>
  <p>Records carry <code>last_verified_at</code>. A record older than
  <strong>{STALE_DAYS} days</strong> is flagged as stale in the report, on the
  grounds that a statutory rate can change at a fiscal event without notice. A
  stale record still produces a figure, with the staleness stated.</p>
</section>
"""


def territory_section_html(rows: list[dict], columns: list[str],
                          by_territory: dict[str, list[dict]]) -> str:
    """One block per programme: every populated field, then worked examples."""
    parts: list[str] = []
    grouped_cols = {c for _, cols in FIELD_GROUPS for c in cols}

    for row in rows:
        programme = row.get("program") or "Unnamed programme"
        territory = row.get("territory") or "Unknown territory"
        parts.append(f'<section class="prog"><h3>{esc(programme)}</h3>')

        badges = []
        if row.get("status"):
            badges.append(esc(str(row["status"]).title()))
        if row.get("is_supplementary"):
            badges.append("Supplementary")
        if row.get("rate_type"):
            badges.append(esc(row["rate_type"].replace("_", " ")))
        if badges:
            parts.append('<p class="badges">' + " &middot; ".join(badges) + "</p>")

        for title, cols in FIELD_GROUPS:
            present = [c for c in cols if c in row and not is_empty(row[c])]
            if not present:
                continue
            parts.append(f'<h4>{esc(title)}</h4><table class="fields"><tbody>')
            for col in present:
                parts.append(
                    f"<tr><th>{esc(col)}</th><td>{pretty_value(col, row[col])}</td></tr>"
                )
            parts.append("</tbody></table>")

        leftovers = [
            c for c in columns
            if c not in grouped_cols and c not in _HIDE_ALWAYS
            and c not in EXCLUDED_COLUMNS and c in row and not is_empty(row[c])
        ]
        if leftovers:
            parts.append('<h4>Other recorded fields</h4><table class="fields"><tbody>')
            for col in leftovers:
                parts.append(
                    f"<tr><th>{esc(col)}</th><td>{pretty_value(col, row[col])}</td></tr>"
                )
            parts.append("</tbody></table>")

        parts.append(worked_examples_html(row, territory, by_territory))
        parts.append("</section>")

    return "".join(parts)


def worked_examples_html(row: dict, territory: str,
                         by_territory: dict[str, list[dict]]) -> str:
    """Run the production engine on this row at each example budget."""
    parts = ['<h4>Worked calculation</h4>',
             '<table class="grid calc"><thead><tr>'
             '<th>Budget (GBP)</th><th>Qualifying spend</th><th>Effective rate</th>'
             '<th>Gross rebate</th><th>Net rebate</th><th>Engine notes</th>'
             '</tr></thead><tbody>']

    any_figure = False
    for budget in EXAMPLE_BUDGETS_GBP:
        try:
            result = ReportValidator._compute_corrected_rebate(
                dict(row), budget, by_territory,
            )
        except Exception as exc:  # a bad row must not sink the document
            parts.append(
                f'<tr><td class="num">{money(budget)}</td>'
                f'<td colspan="5" class="none">calculation raised: {esc(exc)}</td></tr>'
            )
            continue

        if result is None:
            parts.append(
                f'<tr><td class="num">{money(budget)}</td>'
                f'<td colspan="5" class="none">'
                f'{esc(no_figure_reason(row))}</td></tr>'
            )
            continue

        any_figure = True
        notes = [
            result.get("programme_note"), result.get("qualifying_spend_note"),
            result.get("atl_deduction_note"), result.get("rebate_cap_note"),
        ]
        note_html = "<br>".join(esc(n) for n in notes if n) or "no adjustments"
        qs_pct = result.get("qualifying_spend_pct") or 0
        parts.append(
            f'<tr><td class="num">{money(budget)}</td>'
            f'<td class="num">{money(result["qualifying_spend"])}'
            f'<span class="sub">{qs_pct:.0f}% of budget</span></td>'
            f'<td class="num">{result["rate_gross"]:.2f}% gross'
            f'<span class="sub">{result["rate_net"]:.2f}% net</span></td>'
            f'<td class="num">{money(result["gross_rebate"])}</td>'
            f'<td class="num strong">{money(result["net_rebate"])}</td>'
            f'<td class="note">{note_html}</td></tr>'
        )

    parts.append("</tbody></table>")
    if not any_figure:
        parts.append(
            '<p class="caution">This programme produces no computed rebate at any '
            'example budget. The reason is stated in the table above. It is '
            'presented in reports without a figure rather than with an estimated '
            'one.</p>'
        )
    return "".join(parts)


def no_figure_reason(row: dict) -> str:
    """Why the engine returned nothing, read from the row rather than guessed."""
    # A non-entitlement mechanism is the most informative reason there is, and it
    # is a statement about the programme rather than about missing data, so it is
    # checked before anything else.
    mechanism = mechanism_no_figure_reason(row)
    if mechanism:
        engine = str(row.get("qs_engine_type") or "").strip().upper()
        return f"No figure ({engine.replace('_', ' ').lower()}): {mechanism}"

    gross = row.get("rate_gross")
    net = row.get("rate_net")
    if not gross and not net:
        return "No figure: the record carries no usable rate."
    qs_type = (row.get("qualifying_spend_type") or "").lower()
    if qs_type in {"labour", "pdv"} and not row.get("qualifying_spend_labour_pct"):
        return (
            f"No figure: this is a {qs_type} basis programme and the record carries "
            "no sourced share of budget to apply the rate to. A figure would "
            "require inventing that share."
        )
    return "No figure: the engine declined to produce one for this record."


def summary_table_html(rows: list[dict]) -> str:
    """One line per programme, so a reviewer can scan the whole system."""
    parts = ['<section class="page"><h1>All programmes at a glance</h1>',
             '<p class="lede">Every active record in the system. Rates are as stored. '
             'A blank cell means the field is not populated for that programme.</p>',
             '<table class="grid dense"><thead><tr>'
             '<th>Territory</th><th>Programme</th><th>Gross</th><th>Net</th>'
             '<th>Basis</th><th>QS cap</th><th>Rebate ceiling</th>'
             '<th>Verified</th></tr></thead><tbody>']
    for row in rows:
        qs_cap = []
        if row.get("qualifying_spend_cap_pct"):
            qs_cap.append(f"{float(row['qualifying_spend_cap_pct']):g}%")
        if row.get("qualifying_spend_cap_amount"):
            qs_cap.append(money(float(row["qualifying_spend_cap_amount"]),
                                row.get("qualifying_spend_cap_currency") or "GBP"))
        ceiling = (
            money(float(row["rebate_cap_amount"]), row.get("rebate_cap_currency") or "GBP")
            if row.get("rebate_cap_amount") else ""
        )
        parts.append(
            "<tr>"
            f"<td>{esc(row.get('territory'))}</td>"
            f"<td>{esc(row.get('program'))}</td>"
            f"<td class='num'>{esc(row.get('rate_gross') or '')}</td>"
            f"<td class='num'>{esc(row.get('rate_net') or '')}</td>"
            f"<td>{esc(row.get('qualifying_spend_type') or '')}</td>"
            f"<td class='num'>{esc(' + '.join(qs_cap))}</td>"
            f"<td class='num'>{esc(ceiling)}</td>"
            f"<td>{esc(str(row.get('last_verified_at') or '')[:10])}</td>"
            "</tr>"
        )
    parts.append("</tbody></table></section>")
    return "".join(parts)


def non_claimable_html(rows: list[dict]) -> str:
    """Records the engine will not produce a figure from, and why.

    Omitting these would leave a reader believing the system covers fewer
    territories than it does, and would hide the more useful fact: that a
    territory is on file and is currently not claimable. Suspended provincial
    schemes and unverified placeholders are exactly what a financial reviewer
    needs to see stated rather than absent.
    """
    if not rows:
        return ""
    body = []
    for row in rows:
        reason = row.get("notes") or row.get("eligibility_notes") or ""
        warnings = row.get("warnings_json")
        if not reason and warnings:
            try:
                parsed = json.loads(warnings) if isinstance(warnings, str) else warnings
                if isinstance(parsed, list) and parsed:
                    reason = str(parsed[0])
            except (ValueError, TypeError):
                pass
        body.append(
            "<tr>"
            f"<td>{esc(row.get('territory'))}</td>"
            f"<td>{esc(row.get('program'))}</td>"
            f"<td>{esc(str(row.get('status') or '').replace('_', ' '))}</td>"
            f"<td class='note'>{esc(reason[:400])}</td>"
            "</tr>"
        )
    return f"""
<section class="page">
  <h1>Territories on file with no claimable programme</h1>
  <p class="lede">These records are held in the system but are not in a state
  that produces a rebate figure. They are listed so the coverage of this document
  is complete, and because a suspended or unverified programme is a material fact
  in its own right.</p>
  <table class="grid">
    <thead><tr><th>Territory</th><th>Programme</th><th>Status</th><th>Recorded reason</th></tr></thead>
    <tbody>{"".join(body)}</tbody>
  </table>
  <p class="caution">A status other than active means the engine will not select
  the programme and no figure is modelled for that territory. Verify the current
  position with the awarding authority before relying on the absence.</p>
</section>
"""


def glossary_html() -> str:
    rows = "".join(
        f"<tr><th>{esc(field)}</th><td>{esc(meaning)}</td></tr>"
        for field, meaning in FIELD_GLOSSARY
    )
    return f"""
<section class="page">
  <h1>Field reference</h1>
  <p class="lede">The fields that carry calculation meaning. Column names are
  given as stored so a figure in this document can be traced to a database
  field.</p>
  <table class="fields wide"><tbody>{rows}</tbody></table>
</section>
"""


# ── shell ────────────────────────────────────────────────────────────────────


def logo_data_uri() -> str | None:
    path = _REPO / "app" / "templates" / "pdf" / "assets" / "prodculator_logo.jpg"
    try:
        return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None


CSS = """
@page { size: A4; margin: 20mm 16mm 18mm 16mm; }
:root {
  --ink:#14181f; --grey:#5b6472; --hair:#d8dde5; --gold:#8a6d1f;
  --serif:'Source Serif 4','Georgia','Times New Roman',serif;
  --sans:'Montserrat','Segoe UI',-apple-system,'Helvetica Neue',sans-serif;
}
* { box-sizing:border-box; }
body {
  font-family:var(--serif); color:var(--ink); font-size:9.6pt; line-height:1.55;
  margin:0; background:#fff;
}
.watermark {
  position:fixed; top:0; left:0; width:100%; height:100%;
  z-index:0; pointer-events:none;
  display:flex; align-items:center; justify-content:center;
}
.watermark span {
  font-family:var(--sans); font-size:15pt; font-weight:700; letter-spacing:.32em;
  color:rgba(20,24,31,0.055); transform:rotate(-32deg); text-align:center;
  line-height:2.4; text-transform:uppercase; white-space:pre-line;
}
.content { position:relative; z-index:1; }
h1,h2,h3,h4 { font-family:var(--sans); font-weight:700; color:var(--ink); }
h1 { font-size:17pt; letter-spacing:-.01em; margin:0 0 10px; padding-bottom:8px;
     border-bottom:2px solid var(--ink); }
h2 { font-size:11.5pt; margin:20px 0 6px; }
h3 { font-size:12pt; margin:0 0 4px; color:var(--gold); }
h4 { font-size:8.6pt; margin:14px 0 5px; text-transform:uppercase;
     letter-spacing:.13em; color:var(--grey); }
p { margin:0 0 8px; }
.lede { color:var(--grey); font-size:10pt; margin-bottom:14px; }
code { font-family:'Consolas','SF Mono',monospace; font-size:8.6pt;
       background:#f2f4f7; padding:1px 4px; border-radius:2px; }
.page { page-break-before:always; }
.cover { page-break-after:always; text-align:left; padding-top:38mm; }
.cover img { width:190px; margin-bottom:34mm; }
.cover .title { font-family:var(--sans); font-size:29pt; font-weight:800;
                line-height:1.12; letter-spacing:-.02em; margin-bottom:12px; }
.cover .sub { font-size:12pt; color:var(--grey); margin-bottom:30mm; max-width:120mm; }
.cover .meta { border-top:2px solid var(--ink); padding-top:12px; font-size:9pt;
               color:var(--grey); }
.cover .meta b { color:var(--ink); font-family:var(--sans); }
.notice { border:1px solid var(--hair); border-left:3px solid var(--gold);
          padding:12px 14px; margin:14px 0; background:#fbfaf7; font-size:9.2pt; }
table { width:100%; border-collapse:collapse; margin:6px 0 12px; }
.grid th, .grid td { border:1px solid var(--hair); padding:5px 7px;
                     text-align:left; vertical-align:top; }
.grid thead th { background:#f2f4f7; font-family:var(--sans); font-size:8pt;
                 text-transform:uppercase; letter-spacing:.08em; color:var(--grey); }
.grid.narrow { width:56mm; }
.grid.dense th, .grid.dense td { padding:3.5px 5px; font-size:8.3pt; }
.fields th, .fields td { border-bottom:1px solid var(--hair); padding:4px 7px;
                         text-align:left; vertical-align:top; }
.fields th { width:52mm; font-family:var(--sans); font-size:8.1pt; font-weight:600;
             color:var(--grey); word-break:break-word; }
.fields.wide th { width:56mm; }
.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
.num.strong { font-weight:700; }
.sub { display:block; font-size:7.6pt; color:var(--grey); font-weight:400; }
.note { font-size:8pt; color:var(--grey); }
.none { color:var(--grey); font-style:italic; }
.caution { border-left:3px solid var(--gold); padding:7px 11px; background:#fbfaf7;
           font-size:8.8pt; margin:6px 0 12px; }
.formula { font-family:'Consolas','SF Mono',monospace; font-size:9pt;
           background:#f2f4f7; border-left:3px solid var(--gold);
           padding:9px 12px; margin:9px 0; }
.terr { page-break-before:always; }
.terr > h1 { margin-bottom:14px; }
.prog { border-top:1px solid var(--hair); padding-top:12px; margin-top:16px;
        page-break-inside:avoid; }
.prog:first-of-type { border-top:none; padding-top:0; margin-top:0; }
.badges { font-family:var(--sans); font-size:8pt; color:var(--grey);
          text-transform:uppercase; letter-spacing:.09em; margin-bottom:10px; }
ul { margin:0 0 8px; padding-left:16px; }
ul.mini { margin:0; padding-left:13px; font-size:8.6pt; }
ol { margin:0 0 8px; padding-left:18px; }
.foot { margin-top:16px; padding-top:8px; border-top:1px solid var(--hair);
        font-size:7.8pt; color:var(--grey); }
"""


def build_html(rows: list[dict], other_rows: list[dict], columns: list[str],
               db_label: str) -> str:
    by_territory: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_territory[row.get("territory") or "Unknown"].append(row)

    logo = logo_data_uri()
    logo_tag = f'<img src="{logo}" alt="Prodculator">' if logo else "<h2>Prodculator</h2>"
    today = date.today().strftime("%d %B %Y")
    territories = sorted(by_territory)

    terr_html = []
    for territory in territories:
        programmes = sorted(by_territory[territory],
                            key=lambda r: (bool(r.get("is_supplementary")),
                                           r.get("program") or ""))
        terr_html.append(
            f'<section class="terr"><h1>{esc(territory)}</h1>'
            f'<p class="lede">{len(programmes)} '
            f'{"programme" if len(programmes) == 1 else "programmes"} on record.</p>'
            + territory_section_html(programmes, columns, by_territory)
            + "</section>"
        )

    stale_note = (
        f"Records are flagged stale in reports once they are older than "
        f"{STALE_DAYS} days."
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Prodculator Territory Incentive and Calculation Reference</title>
<style>{CSS}</style></head>
<body>
<div class="watermark"><span>Property of DeoMedia and Prodculator
Confidential</span></div>
<div class="content">

<section class="cover">
  {logo_tag}
  <div class="title">Territory Incentive and<br>Calculation Reference</div>
  <div class="sub">Every production incentive programme held in the Prodculator
  system, the values that drive each calculation, and the method by which a
  rebate figure is produced.</div>
  <div class="meta">
    <p><b>Prepared for</b> financial and production finance review</p>
    <p><b>Generated</b> {today}</p>
    <p><b>Data source</b> {esc(db_label)}, {len(rows)} claimable programme
       records across {len(territories)} territories, plus {len(other_rows)}
       records held with no claimable programme</p>
    <p><b>Confidentiality</b> Property of DeoMedia Limited and Prodculator. Not
       for distribution.</p>
  </div>
</section>

<section class="page">
  <h1>Scope and basis of preparation</h1>
  <p class="lede">This document is generated directly from the system. It is not
  a hand written summary of it.</p>

  <h2>What the figures are</h2>
  <p>Programme values are read from the <code>incentive_programs</code> table at
  the moment of generation. No value has been transcribed, rounded or restated.
  Where a field is absent from a programme record it is absent from this
  document, which is why the field tables differ in length between programmes.</p>
  <p>Every worked calculation is produced by calling the production rebate
  engine on the real programme record, at the budgets stated. The figures shown
  are the figures a Prodculator report would produce for the same inputs,
  including the cases where the engine declines to produce a figure at all.</p>

  <h2>What the figures are not</h2>
  <div class="notice">
    <p>These are modelled estimates from programme rules, not advice and not a
    substitute for a production accountant or a tax adviser. Three limits matter
    to a financial reader.</p>
    <p><b>Above the line costs are estimated, not itemised.</b> Where a
    programme excludes above the line spend, the engine deducts
    {DEFAULT_ATL_PCT:.0%} of budget as a standing assumption. A production whose
    cast and director costs sit materially away from that figure will diverge.</p>
    <p><b>Per person caps are flagged, not modelled.</b> The system holds no
    payroll breakdown, so a per individual wage or fee ceiling is surfaced as a
    risk rather than applied per head. On productions with expensive talent this
    understates the reduction.</p>
    <p><b>Local spend is assumed, not verified.</b> For a local spend basis the
    engine assumes the full qualifying spend is incurred in territory. A partial
    shoot will qualify for less.</p>
  </div>

  <h2>Currency</h2>
  <p>Calculations are performed in sterling. Budgets are converted at the live
  rate where available. Rebate ceilings held in another currency are converted
  the same way, falling back to a static table that errs toward a smaller
  sterling ceiling. Both the table and the conversion point are set out in the
  method section.</p>

  <h2>Freshness and provenance</h2>
  <p>Each programme record carries the authority it was taken from, a source
  URL, and the date it was last verified. {stale_note} Those fields are
  reproduced for every programme so a reviewer can judge the weight to place on
  a figure and go to the primary source.</p>
  <p>Data team annotations held against these records are excluded from this
  document by design.</p>
</section>

{methodology_html()}

{summary_table_html(rows)}

{"".join(terr_html)}

{non_claimable_html(other_rows)}

{glossary_html()}

<p class="foot">Prodculator Territory Incentive and Calculation Reference.
Generated {today} from {len(rows)} programme records. Property of DeoMedia
Limited and Prodculator. Confidential, not for distribution.</p>
</div>
</body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(_REPO / "incentive_reference.html"))
    args = parser.parse_args()

    db_url = os.environ.get("DB_URL")
    if not db_url:
        print("DB_URL is not set. This document is generated from the live table, "
              "not from the migration history.", file=sys.stderr)
        return 2

    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        inspector = sa.inspect(conn)
        columns = [c["name"] for c in inspector.get_columns("incentive_programs")]
        selectable = [c for c in columns if c not in EXCLUDED_COLUMNS]
        query = (
            f'SELECT {", ".join(chr(34) + c + chr(34) for c in selectable)} '
            f'FROM incentive_programs ORDER BY territory, program'
        )
        all_rows = [coerce_row(dict(r)) for r in conn.execute(sa.text(query)).mappings()]

    # Active records get the full treatment; the rest are listed with their
    # status so the document's coverage is complete either way.
    def _is_active(row: dict) -> bool:
        status = row.get("status")
        return status is None or str(status).strip().lower() == "active"

    rows = [r for r in all_rows if _is_active(r)]
    other_rows = [r for r in all_rows if not _is_active(r)]

    if not rows:
        print("No programme records found.", file=sys.stderr)
        return 1

    host = re.sub(r"://[^@]*@", "://", db_url).split("/")[-1]
    doc = strip_em_dashes(
        build_html(rows, other_rows, selectable, f"incentive_programs ({host})")
    )

    out = Path(args.out)
    out.write_text(doc, encoding="utf-8")
    print(f"Wrote {out} ({len(doc):,} bytes, {len(rows)} claimable + "
          f"{len(other_rows)} non-claimable programmes)")

    try:
        from weasyprint import HTML

        pdf_path = out.with_suffix(".pdf")
        HTML(string=doc, base_url=str(_REPO)).write_pdf(str(pdf_path))
        print(f"Wrote {pdf_path}")
    except Exception as exc:
        print(f"PDF not rendered ({type(exc).__name__}). Print the HTML to PDF "
              f"from a browser, or run this where WeasyPrint's native libraries "
              f"are installed.", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
