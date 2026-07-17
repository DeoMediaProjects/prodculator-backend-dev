from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.modules.calculator.schemas import ScenarioRequest
from app.modules.calculator.service import CalculatorService
from app.modules.reports.builder import ReportBuilder, SCORE_WEIGHTS


class _FakeQuery:
    def __init__(self, data: list[dict]):
        self.data = data

    def select(self, *_args, **_kwargs):
        return self

    def execute(self):
        return SimpleNamespace(data=self.data)


class _FakeDB:
    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables
        self.settings = SimpleNamespace()

    def table(self, table_name: str):
        return _FakeQuery(self.tables.get(table_name, []))


class _FakeFX:
    def __init__(self):
        self.currency_advantage_calls: list[tuple[str, str]] = []

    def convert_budget(self, amount: float, from_currency: str, to_currency: str):
        return {
            "converted": amount,
            "rate": 1.0,
            "rate_date": "2026-06-07",
            "from_currency": from_currency,
            "to_currency": to_currency,
        }

    def get_rates_batch(self, _base: str, targets: list[str]):
        return {target: (350.0, date(2026, 6, 7)) for target in targets}

    def compute_currency_advantage_score(self, budget_currency: str, territory_currency: str):
        self.currency_advantage_calls.append((budget_currency, territory_currency))
        return 77, None


def _incentive() -> dict:
    return {
        "program": "Hungary Tax Rebate",
        "territory": "Hungary",
        "rate_gross": 30.0,
        "rate_net": None,
        "rate_type": "rebate",
        "currency": "HUF",
        "qualifying_spend_type": "total",
        "payment_reliability": 0.95,
        "payment_timeline_days_max": 90,
        "payment_timeline_notes": "6-12 weeks",
        "is_supplementary": False,
        "status": "active",
    }


def test_calculator_uses_budget_currency_reliability_and_profile_scores():
    incentive = _incentive()
    db = _FakeDB(
        {
            "incentive_programs": [incentive],
            "territory_profiles": [
                {
                    "territory": "Hungary",
                    "iso_code": "HU",
                    "crew_depth_tier": "established",
                    "crew_depth_score": 80,
                    "infrastructure_tier": "growing",
                    "infrastructure_score": 65,
                },
            ],
        }
    )
    service = CalculatorService(db, db.settings)
    fake_fx = _FakeFX()
    service.fx = fake_fx

    response = service.compute_scenario(
        ScenarioRequest(
            budget_amount=1_000_000,
            budget_currency="USD",
            vfx_pct=0,
            production_format="Feature Film",
            production_priority="full",
            territories=["Hungary"],
            baseline="GB",
        )
    )

    territory = response.territories[0]
    expected_strength = ReportBuilder._compute_incentive_strength(incentive)
    expected_reliability, _ = ReportBuilder._compute_reliability(incentive)
    expected_overall = (
        SCORE_WEIGHTS["full"]["incentiveStrength"] * expected_strength
        + SCORE_WEIGHTS["full"]["incentiveReliability"] * expected_reliability
        # Cost efficiency: no curated score on the profile -> neutral 50
        # (crew day-rate derivation removed 2026-07, owner-approved)
        + SCORE_WEIGHTS["full"]["costEfficiency"] * 50
        + SCORE_WEIGHTS["full"]["currencyAdvantage"] * 77
        + SCORE_WEIGHTS["full"]["crewDepth"] * 80
        + SCORE_WEIGHTS["full"]["infrastructure"] * 65
    )

    assert fake_fx.currency_advantage_calls == [("USD", "HUF")]
    assert territory.cost_efficiency_score is None
    assert territory.crew_depth_score == 80
    assert territory.crew_depth_tier == "established"
    assert territory.infrastructure_score == 65
    assert territory.infrastructure_tier == "growing"
    assert territory.financial_return_score == round(
        (expected_strength + expected_reliability) * 0.5
    )
    assert territory.overall_score == round(expected_overall, 1)


def test_calculator_displays_net_rate_and_net_rebate():
    incentive = _incentive()
    incentive["rate_gross"] = 30.0
    incentive["rate_net"] = 20.0
    incentive["currency"] = "USD"
    db = _FakeDB(
        {
            "incentive_programs": [incentive],
                "territory_profiles": [],
        }
    )
    service = CalculatorService(db, db.settings)
    fake_fx = _FakeFX()
    service.fx = fake_fx

    response = service.compute_scenario(
        ScenarioRequest(
            budget_amount=1_000_000,
            budget_currency="USD",
            vfx_pct=0,
            production_format="Feature Film",
            production_priority="full",
            territories=["Hungary"],
            baseline="GB",
        )
    )

    territory = response.territories[0]
    assert territory.rate_display == "20% net (30% gross)"
    assert territory.estimated_rebate_display == "$200,000"
    assert territory.estimated_rebate == 200_000
