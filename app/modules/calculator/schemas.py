"""What-If Calculator request / response schemas."""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class ScenarioRequest(BaseModel):
    budget_amount: float = Field(
        ..., gt=0, le=100_000_000,
        description="Production budget in budget_currency",
    )
    budget_currency: Literal[
        "GBP", "USD", "EUR", "ZAR", "CAD", "AUD",
        "NGN", "HUF", "CZK", "MAD", "NZD", "RON", "RSD",
    ] = "GBP"
    vfx_pct: float = Field(
        0, ge=0, le=100,
        description="VFX allocation as percentage of total budget",
    )
    production_format: Literal[
        "Feature Film", "Short Film", "TV Series", "Limited Series",
        "Mini-Series", "Documentary", "Docuseries", "Animation",
        "Animated Feature", "Animation Series",
    ] | None = "Feature Film"
    production_priority: Literal["incentive", "full", "location"] = "full"
    territories: list[str] | None = Field(
        None,
        description="Filter to specific territories (null = all covered)",
    )


class TerritoryScenario(BaseModel):
    territory: str
    iso: str | None = None
    programme: str
    rate_display: str
    rate_gross: float | None = None
    rate_net: float | None = None
    rate_type: str | None = None
    estimated_rebate: float
    estimated_rebate_display: str
    qualifying_spend: float
    qualifying_spend_display: str
    qualifying_spend_pct: float
    atl_deduction: float | None = None
    atl_deduction_display: str | None = None
    currency_advantage_score: int
    currency_advantage_warning: str | None = None
    territory_currency: str
    fx_rate: float | None = None
    fx_rate_date: str | None = None
    crew_cost_index: float | None = None
    crew_rates: dict[str, str] = {}
    net_saving: float
    net_saving_display: str
    payment_timeline: str | None = None
    min_spend: str | None = None
    cap: str | None = None
    eligibility_note: str | None = None
    programme_note: str | None = None
    overall_score: float
    vfx_uplift_rate: float | None = None
    vfx_uplift_programme: str | None = None
    vfx_uplift_value: float | None = None
    vfx_uplift_display: str | None = None


class ScenarioResponse(BaseModel):
    budget_amount: float
    budget_currency: str
    budget_gbp: float
    vfx_pct: float
    production_format: str | None = None
    production_priority: str
    fx_rates_date: str | None = None
    territories: list[TerritoryScenario]
