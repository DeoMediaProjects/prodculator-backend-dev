"""Comparing a legacy report against the v2 orchestration of the same run.

WHAT A COMPARISON IS FOR HERE
-----------------------------
The plan's acceptance step asks for old-versus-v2 output compared for
contradictions, missing-source claims and financial overstatement. The
temptation is to treat that as a diff that should come out empty.

It should not. The two shapes are expected to disagree, and the disagreements
are the deliverable. When the legacy report quotes a £5.74M rebate and v2 says
REQUIRES_COST_BREAKDOWN for the same territory, that is not a regression — it is
the entire point of the rebuild, visible in one line. A comparison that reported
it as a failure would train whoever reads it to ignore the output.

So every finding carries an ``expected`` flag. Expected findings are the rebuild
working: figures withdrawn because their statutory basis was never supplied,
records moved out of Grants by routing, pipeline money removed from a committed
total. Unexpected findings are the ones that need a human: v2 asserting
something legacy did not, or a section losing content with no rule to explain it.

WHAT IT DOES NOT DO
-------------------
It does not decide which version is right, and it does not pass or fail a
cutover. It produces the evidence a reviewer needs, and a reviewer signs the
cutover off. An automated verdict here would be a machine approving its own
replacement of the thing it is comparing against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: A legacy report quotes a figure that v2 declines to produce, because the
#: statutory base was never supplied. The rebuild working.
FIGURE_WITHDRAWN = "FIGURE_WITHDRAWN"
#: A record the legacy report listed under Grants that v2 routes elsewhere.
ROUTING_MOVED = "ROUTING_MOVED"
#: The legacy report implies committed money that v2's finance buckets do not
#: hold as committed.
COMMITTED_FINANCE_WITHDRAWN = "COMMITTED_FINANCE_WITHDRAWN"
#: v2 carries a figure the legacy report did not. Never expected: the rebuild
#: removes claims, it does not add them.
FIGURE_ADDED = "FIGURE_ADDED"
#: A section that had legacy content and has no v2 content, with no rule
#: explaining the loss.
SECTION_EMPTIED = "SECTION_EMPTIED"

#: Which findings are the rebuild working as designed.
_EXPECTED_KINDS: frozenset[str] = frozenset(
    {FIGURE_WITHDRAWN, ROUTING_MOVED, COMMITTED_FINANCE_WITHDRAWN}
)

#: A money-shaped string, in any of the currencies the report uses. Deliberately
#: loose: this is looking for "did a number that reads as money appear here",
#: and a false positive costs a reviewer one glance while a false negative hides
#: exactly the overstatement the comparison exists to surface.
_MONEY = re.compile(r"[£$€]\s?\d[\d,.]*\s?[MBK]?", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    kind: str
    where: str
    detail: str
    legacy_value: Any = None
    v2_value: Any = None

    @property
    def expected(self) -> bool:
        return self.kind in _EXPECTED_KINDS


@dataclass
class ComparisonReport:
    findings: list[Finding] = field(default_factory=list)
    legacy_sections_with_content: int = 0
    v2_sections_with_content: int = 0

    @property
    def expected(self) -> list[Finding]:
        return [f for f in self.findings if f.expected]

    @property
    def needs_review(self) -> list[Finding]:
        return [f for f in self.findings if not f.expected]

    @property
    def has_unexpected(self) -> bool:
        return bool(self.needs_review)


def _money_in(value: Any) -> set[str]:
    """Every money-shaped string anywhere inside a nested structure."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(match.group(0).strip() for match in _MONEY.finditer(value))
    elif isinstance(value, Mapping):
        for item in value.values():
            found |= _money_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= _money_in(item)
    return found


def _v2_section(payload: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    for section in payload.get("sections") or ():
        if section.get("section_key") == key:
            return section
    return None


def _v2_records(payload: Mapping[str, Any], key: str) -> list[Any]:
    section = _v2_section(payload, key)
    if not section:
        return []
    return [
        record
        for block in section.get("blocks") or ()
        for record in (block.get("data") or {}).get("recommendations") or ()
    ]


def _names(records: Iterable[Any]) -> set[str]:
    names: set[str] = set()
    for record in records:
        if isinstance(record, Mapping):
            for key in ("name", "title", "programme", "company", "festival"):
                value = record.get(key)
                if value:
                    names.add(str(value).strip())
                    break
        elif record:
            names.add(str(record).strip())
    return names


def compare(legacy: Mapping[str, Any], payload: Mapping[str, Any]) -> ComparisonReport:
    """Compare one legacy report against the v2 payload from the same run."""
    report = ComparisonReport()

    # ── Figures the legacy report carries and v2 does not ────────────────────
    legacy_money = _money_in(legacy.get("incentiveEstimates")) | _money_in(
        legacy.get("financialAnalysis")
    )
    v2_incentives = _v2_records(payload, "tax_incentive_analysis")
    v2_money = _money_in(v2_incentives)

    for amount in sorted(legacy_money - v2_money):
        report.findings.append(
            Finding(
                FIGURE_WITHDRAWN,
                "tax_incentive_analysis",
                "The legacy report quotes this figure; v2 produces no amount, "
                "because the statutory qualifying base was not supplied.",
                legacy_value=amount,
            )
        )

    # The reverse is never expected. The rebuild withdraws claims that were not
    # supported; a figure appearing only in v2 means something was computed from
    # a basis the legacy path did not have, which needs explaining.
    for amount in sorted(v2_money - legacy_money):
        report.findings.append(
            Finding(
                FIGURE_ADDED,
                "tax_incentive_analysis",
                "v2 carries a figure the legacy report does not. The rebuild "
                "removes unsupported claims rather than adding new ones, so this "
                "needs an explanation.",
                v2_value=amount,
            )
        )

    # ── Routing ──────────────────────────────────────────────────────────────
    legacy_grants = _names(legacy.get("fundingOpportunities") or ())
    v2_markets = _names(_v2_records(payload, "industry_development_market_strategy"))
    for name in sorted(legacy_grants & v2_markets):
        report.findings.append(
            Finding(
                ROUTING_MOVED,
                "grant_funding_opportunities",
                "Listed under Grants in the legacy report; v2 routes it to "
                "Markets/Labs/WIP. A market is not a grant.",
                legacy_value=name,
            )
        )

    # ── Committed finance ────────────────────────────────────────────────────
    readiness = payload.get("financial_readiness") or {}
    committed = readiness.get("documented_committed_finance") or []
    summary = legacy.get("executiveSummary") or {}
    headline = summary.get("headlineNetBudget") or summary.get(
        "recommendedTerritoryRebate"
    )
    if headline and not committed:
        report.findings.append(
            Finding(
                COMMITTED_FINANCE_WITHDRAWN,
                "financial_readiness",
                "The legacy summary states a net position; v2 holds nothing as "
                "committed finance, because no item carries documented award "
                "evidence.",
                legacy_value=headline,
            )
        )

    # ── Section coverage ─────────────────────────────────────────────────────
    coverage = {
        "grant_funding_opportunities": legacy.get("fundingOpportunities"),
        "festival_strategy": legacy.get("festivalRecommendations"),
        "sales_distribution_strategy": legacy.get("distributorRecommendations"),
        "comparable_productions": legacy.get("comparables"),
        "tax_incentive_analysis": legacy.get("incentiveEstimates"),
    }
    for key, legacy_content in coverage.items():
        has_legacy = bool(legacy_content)
        has_v2 = bool(_v2_records(payload, key))
        report.legacy_sections_with_content += int(has_legacy)
        report.v2_sections_with_content += int(has_v2)
        if has_legacy and not has_v2:
            # Routing and withdrawn figures already explain their own losses. A
            # section emptied with neither explanation is the one a reviewer has
            # to look at.
            explained = any(
                f.where == key and f.expected for f in report.findings
            )
            if not explained:
                report.findings.append(
                    Finding(
                        SECTION_EMPTIED,
                        key,
                        "The legacy report has content here and v2 has none, with "
                        "no routing or withdrawn figure to explain it.",
                        legacy_value=len(legacy_content),
                    )
                )

    return report


def render(report: ComparisonReport) -> str:
    lines = [
        "Legacy versus v2 orchestration",
        "",
        "This does not decide which version is right and does not pass or fail a",
        "cutover. It produces the evidence; a reviewer signs the cutover off.",
        "",
        f"  Sections with legacy content: {report.legacy_sections_with_content}",
        f"  Sections with v2 content:     {report.v2_sections_with_content}",
        f"  Expected differences:         {len(report.expected)}",
        f"  Needing review:               {len(report.needs_review)}",
        "",
    ]

    if report.expected:
        lines.append("Expected — the rebuild working as designed:")
        for finding in report.expected:
            lines.append(f"  [{finding.kind}] {finding.where}")
            lines.append(f"      {finding.detail}")
            if finding.legacy_value is not None:
                lines.append(f"      legacy: {finding.legacy_value}")
        lines.append("")

    if report.needs_review:
        lines.append("Needing review — not explained by any rule:")
        for finding in report.needs_review:
            lines.append(f"  [{finding.kind}] {finding.where}")
            lines.append(f"      {finding.detail}")
            if finding.legacy_value is not None:
                lines.append(f"      legacy: {finding.legacy_value}")
            if finding.v2_value is not None:
                lines.append(f"      v2:     {finding.v2_value}")
        lines.append("")
    else:
        lines.append("Nothing unexplained. Every difference has a rule behind it.")

    return "\n".join(lines)
