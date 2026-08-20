"""The What-If calculator must honour the same stacking rules as the report.

The calculator computed the VFX uplift as ``budget x vfx_pct x supp_rate`` and
added it to net saving unconditionally — it imported neither ``stacking`` nor
``project_incentive``. The UK VFX row states "Cannot combine with the IFTC
enhanced rate", which the reports path honours through ``resolve_stacking``, so
for a UK project the calculator and the report disagreed about the same pair and
the calculator was the one overstating.

These tests pin the two surfaces to one answer.
"""
from __future__ import annotations

from app.modules.reports.stacking import resolve_stacking


_IFTC = "AVEC (Enhanced/IFTC)"
_VFX = "UK VFX Expenditure Credit (Uplift)"


def _vfx_row(qs_basis: str) -> dict:
    return {
        "territory": "United Kingdom",
        "program": _VFX,
        "is_supplementary": True,
        "rate_gross": 39.0,
        "rate_net": 29.25,
        "qs_basis": qs_basis,
    }


def _primary(program: str) -> dict:
    return {"territory": "United Kingdom", "program": program, "rate_gross": 53.0}


class TestVfxUpliftIsGated:
    def test_iftc_and_vfx_are_mutually_exclusive(self):
        """The live row's own wording must be read as an exclusion."""
        result = resolve_stacking(
            _primary(_IFTC),
            _vfx_row(
                "80% cap REMOVED for qualifying VFX expenditure specifically "
                "(from 1 April 2025). Cannot combine with the IFTC enhanced rate "
                "or animation uplift"
            ),
            primary_name=_IFTC,
            supplementary_name=_VFX,
        )
        assert result["relationship"] == "mutually_exclusive"
        assert result["stacks"] is False
        assert result["note"]

    def test_standard_avec_and_vfx_do_stack(self):
        """The exclusion names the ENHANCED rate. Standard AVEC is not excluded —
        treating it as excluded would understate a real entitlement."""
        result = resolve_stacking(
            _primary("UK Audio-Visual Expenditure Credit (AVEC)"),
            _vfx_row(
                "80% cap REMOVED for qualifying VFX expenditure specifically. "
                "Cannot combine with the IFTC enhanced rate"
            ),
            primary_name="UK Audio-Visual Expenditure Credit (AVEC)",
            supplementary_name=_VFX,
        )
        assert result["stacks"] is True

    def test_supplementary_with_no_stated_constraint_stacks(self):
        """A supplementary credit exists to be added to something."""
        result = resolve_stacking(
            _primary("Some Primary Credit"),
            _vfx_row("Applies to qualifying VFX expenditure."),
            primary_name="Some Primary Credit",
            supplementary_name=_VFX,
        )
        assert result["stacks"] is True


class TestCalculatorConsultsStacking:
    def test_calculator_imports_the_shared_resolver(self):
        """A structural guard: the defect was an absent import, so the absence of
        the import is the thing worth failing on. If the uplift is ever computed
        somewhere else, this test should be moved rather than deleted."""
        from app.modules.calculator import service as calc_service

        assert hasattr(calc_service, "resolve_stacking"), (
            "the calculator must resolve stacking through the same module as "
            "ReportBuilder, or the two surfaces will disagree again"
        )

    def test_scenario_schema_carries_the_stacking_explanation(self):
        """A zero uplift must be explained, not look like missing data."""
        from app.modules.calculator.schemas import TerritoryScenario

        assert "vfx_stacking_note" in TerritoryScenario.model_fields
