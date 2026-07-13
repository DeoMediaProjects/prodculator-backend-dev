"""Territory comparison request / response schemas."""
from __future__ import annotations

from pydantic import BaseModel
from typing import Literal


class TerritoryListItem(BaseModel):
    label: str
    iso: str
    level: Literal["national", "regional"]
    parent: str | None = None


class IncentiveInfo(BaseModel):
    programme: str
    tax_rebate: str
    rate_gross: float | None = None
    rate_net: float | None = None
    rate_type: str | None = None
    post_production_bonus: str | None = None
    min_spend: str | None = None
    min_spend_raw: float | None = None
    min_spend_currency: str | None = None
    cap_display: str | None = None
    payment_timeline: str | None = None
    payment_timeline_days_min: int | None = None
    payment_timeline_days_max: int | None = None
    eligibility_rules: list[str] = []
    warnings: list[str] = []
    last_verified: str | None = None
    expiry_date: str | None = None


class CrewCostInfo(BaseModel):
    avg_day_rate: float | None = None
    avg_day_rate_display: str | None = None
    currency: str = "GBP"
    sample_roles: dict[str, str] = {}


class TerritoryProfileInfo(BaseModel):
    """Maintained crew-depth / infrastructure / bankability profile.

    Tier/score of None means "not yet assessed". cert/payment weeks describe
    incentive certification and payout timing; real_world_confirms is None
    when unconfirmed (distinct from False).
    """

    crew_depth_tier: str | None = None
    crew_depth_score: int | None = None
    crew_depth_notes: str | None = None
    infrastructure_tier: str | None = None
    infrastructure_score: int | None = None
    infrastructure_notes: str | None = None
    cert_weeks_min: int | None = None
    cert_weeks_max: int | None = None
    payment_weeks_min: int | None = None
    payment_weeks_max: int | None = None
    bankability_source_quality: str | None = None
    bankability_source_note: str | None = None
    bankability_real_world_confirms: bool | None = None
    bankability_suspended: bool | None = None
    bankability_source_url: str | None = None


class TerritoryCompareItem(BaseModel):
    label: str
    iso: str
    level: Literal["national", "regional"]
    parent: str | None = None
    incentive: IncentiveInfo | None = None
    crew_costs: CrewCostInfo | None = None
    profile: TerritoryProfileInfo | None = None
    labor_requirement: str | None = None
    highlights: list[str] = []
    restrictions: list[str] = []
    currency: str = "GBP"


class TerritoryCompareResponse(BaseModel):
    territories: list[TerritoryCompareItem]
    available_territories: list[TerritoryListItem]


class TerritoryListResponse(BaseModel):
    territories: list[TerritoryListItem]
