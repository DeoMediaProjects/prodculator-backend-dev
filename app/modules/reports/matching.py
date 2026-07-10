"""Festival & distributor matching engine.

Faithful port of the handoff's ``festival_distributor_matcher.py``
(prodculator_dev_handoff/code) — the deterministic logic the report pipeline
must mirror. Scoring weights are part of the platform spec:

  Festivals:    format + timing are HARD GATES (exclude, never deprioritise);
                genre +2/match, "all" +1; declared-audience +2 (general fest)
                / +3 (focused fest); representation +3 (strict opt-in);
                comparable-production festival +2.5. Tier is display only.
  Distributors: scouts-matched-festival +4 (the strongest real signal);
                genre +2/match, "all" +0.5; market reach +3; declared
                audience +3; representation +3 (opt-in). audience_skew is
                stored, never scored. Only score > 0 returned.

DESIGN PRINCIPLES (verbatim from the handoff):
1. Representation-focused festivals/distributors are ONLY surfaced if the
   user explicitly opted into the corresponding Representation field on
   intake — never an inference from anything else.
2. Timing is a real gate: a festival whose accepted completion window
   doesn't overlap the recommendation window is excluded.
3. Distributor→festival scouting linkage is sourced from distributors' own
   public statements, and only fires when the festival matched first.
4. Comparable-production festival history is a stronger signal than
   genre-matching alone.

Adaptations for this codebase (behaviour-preserving):
- rows come from the DB, so festival fields fall back across column names
  (genres/genre_tags, location/territory);
- ``completion_date=None`` skips the timing gate instead of raising — the
  intake's filming date is optional here; when absent we cannot honestly
  exclude on timing, so we don't.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Completion date estimation
# ---------------------------------------------------------------------------

def estimate_completion_date(
    filming_start: date,
    filming_duration_weeks: float | None,
    estimated_shoot_days: int | None = None,
    post_production_weeks: int = 20,
) -> tuple[date, date, str]:
    """User-declared duration is PRIMARY; AI-estimated shoot days is only a
    fallback (same priority order the backend uses elsewhere)."""
    if filming_duration_weeks is not None:
        shoot_weeks = filming_duration_weeks
        source = "user-declared duration"
    elif estimated_shoot_days is not None:
        shoot_weeks = estimated_shoot_days / 5.5
        source = "AI-estimated shoot days (fallback — no user duration supplied)"
    else:
        raise ValueError(
            "Need either filming_duration_weeks or estimated_shoot_days to calculate completion."
        )

    shoot_end = filming_start + timedelta(weeks=shoot_weeks)
    completion = shoot_end + timedelta(weeks=post_production_weeks)
    logger.info(
        "Completion estimated via %s: shoot ends %s, completion %s",
        source, shoot_end, completion,
    )
    return completion, shoot_end, source


def festival_window(
    completion_date: date, months_after: int = 6, window_months: int = 12
) -> tuple[date, date]:
    """Window OPENS 6 months after completion and STAYS OPEN 12 months."""
    window_start = completion_date + timedelta(days=int(months_after * 30.44))
    window_end = window_start + timedelta(days=int(window_months * 30.44))
    return window_start, window_end


# ---------------------------------------------------------------------------
# Festival matching
# ---------------------------------------------------------------------------

@dataclass
class FestivalMatch:
    festival: dict
    score: float
    reasons: list = field(default_factory=list)


def match_festivals(
    festivals: list[dict],
    *,
    genres: list[str],
    representation_gender: str | None,
    representation_minority: list[str],
    production_format: str,
    completion_date: date | None,
    comparable_production_festivals: list[str] | None = None,
    target_audience: list[str] | None = None,
    audience_segments: list[str] | None = None,
) -> list[FestivalMatch]:
    target_audience = target_audience or []
    audience_segments = audience_segments or []
    declared_audience = {a.lower() for a in target_audience + audience_segments}

    comparable_production_festivals = comparable_production_festivals or []
    if completion_date is not None:
        window_start, window_end = festival_window(completion_date)
    matches: list[FestivalMatch] = []

    for fest in festivals:
        score = 0.0
        reasons: list[str] = []

        eligible_formats = fest.get("eligible_formats") or []

        # --- Hard gate: format eligibility ---
        if production_format.lower() not in [f.lower() for f in eligible_formats]:
            continue  # excluded, not just deprioritized

        # --- Hard gate: timing — festival's own acceptance window must
        # overlap the 6-to-18-month recommendation window ---
        if completion_date is not None:
            min_m = fest.get("min_months_after_completion")
            max_m = fest.get("max_months_after_completion")
            if min_m is not None and max_m is not None:
                fest_earliest = completion_date + timedelta(days=int(min_m * 30.44))
                fest_latest = completion_date + timedelta(days=int(max_m * 30.44))
                if not (fest_earliest <= window_end and fest_latest >= window_start):
                    continue

        # --- Genre match ---
        fest_genres = [
            str(g).lower() for g in (fest.get("genre_tags") or fest.get("genres") or [])
        ]
        if "all" in fest_genres:
            score += 1.0
            reasons.append("Accepts all genres")
        else:
            overlap = [g for g in genres if g.lower() in fest_genres]
            if overlap:
                score += 2.0 * len(overlap)
                reasons.append(f"Genre match: {', '.join(overlap)}")
            else:
                continue  # no genre fit and festival isn't genre-agnostic — exclude

        # --- Declared Target Audience — declared-only, never inferred ---
        fest_aud = {str(a).lower() for a in (fest.get("audience_focus") or [])}
        aud_overlap = declared_audience.intersection(fest_aud)

        # --- Representation match — STRICT OPT-IN ONLY ---
        fest_rep = fest.get("representation_focus") or ["general"]
        if fest_rep == ["general"] and aud_overlap:
            score += 2.0
            reasons.append("Audience focus matches your declared target audience")
        if fest_rep != ["general"]:
            user_rep_signals: set[str] = set()
            if representation_gender and representation_gender.lower() not in ("prefer not to say", ""):
                if representation_gender.lower() == "woman":
                    user_rep_signals.add("women")
                if representation_gender.lower() in ("non-binary",):
                    user_rep_signals.add("lgbtq+")
            for m in representation_minority:
                if m and m.lower() not in ("prefer not to say", ""):
                    if "lgbtq" in m.lower():
                        user_rep_signals.add("lgbtq+")
                    if "racial" in m.lower() or "ethnic" in m.lower():
                        user_rep_signals.add("racial_ethnic_minority")
                    if "disability" in m.lower():
                        user_rep_signals.add("disability")

            rep_overlap = user_rep_signals.intersection(set(fest_rep))
            # A festival whose record declares an audience focus (Frameline /
            # Outfest) can ALSO be surfaced by declared Target Audience — who
            # the FILM is for, not who made it. Still declared-only.
            if rep_overlap:
                score += 3.0
                reasons.append(
                    f"Matches your Representation selection: {', '.join(sorted(rep_overlap))}"
                )
                if aud_overlap:
                    score += 1.0
                    reasons.append(
                        "Your declared target audience also matches this festival's audience focus"
                    )
            elif aud_overlap:
                score += 3.0
                reasons.append(
                    "Your declared target audience matches this festival's audience focus"
                )
            else:
                continue  # targeted festival, no opted-in match — exclude entirely

        # --- Comparable Productions boost ---
        if fest.get("name") in comparable_production_festivals:
            score += 2.5
            reasons.append("A comparable production in your genre/scale played this festival")

        # --- Tier as a tiebreaker note, not a score driver ---
        if fest.get("tier"):
            reasons.append(f"Tier: {fest['tier']}")

        matches.append(FestivalMatch(festival=fest, score=score, reasons=reasons))

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches


# ---------------------------------------------------------------------------
# Distributor matching
# ---------------------------------------------------------------------------

@dataclass
class DistributorMatch:
    distributor: dict
    score: float
    reasons: list = field(default_factory=list)


def match_distributors(
    distributors: list[dict],
    *,
    genres: list[str],
    representation_gender: str | None,
    representation_minority: list[str],
    matched_festival_names: list[str],
    budget_tier: str | None = None,
    target_audience: list[str] | None = None,
    audience_segments: list[str] | None = None,
    audience_skew: str | None = None,
    production_territories: list[str] | None = None,
) -> list[DistributorMatch]:
    target_audience = target_audience or []
    audience_segments = audience_segments or []
    production_territories = production_territories or []
    declared_audience = {a.lower() for a in target_audience + audience_segments}
    _ = audience_skew  # banked for B2B / future matching — intentionally unused in scoring

    matches: list[DistributorMatch] = []

    for dist in distributors:
        score = 0.0
        reasons: list[str] = []

        # --- Genre match ---
        dist_genres = [str(g).lower() for g in (dist.get("specialty_genres") or [])]
        if "all" in dist_genres:
            score += 0.5
            reasons.append("Distributes across all genres")
        else:
            overlap = [g for g in genres if g.lower() in dist_genres]
            if overlap:
                score += 2.0 * len(overlap)
                reasons.append(f"Genre specialty match: {', '.join(overlap)}")

        # --- Market-reach signal — stated reach vs where this production
        # actually lives; never inferred beyond the region synonyms below ---
        if production_territories:
            _reach = [str(t).lower() for t in (dist.get("territory_reach") or [])]
            _prods = [t.lower() for t in production_territories]
            _hit = next(
                (pt for pt in _prods for rt in _reach if pt and (pt in rt or rt in pt)),
                None,
            )
            if _hit is None:
                _regions = {
                    "nigeria": ["africa", "west africa", "pan-african"],
                    "south africa": ["africa", "pan-african"],
                    "united kingdom": ["europe", "uk & ireland"],
                    "hungary": ["europe"],
                    "malta": ["europe"],
                }
                for pt in _prods:
                    for reg in _regions.get(pt, []):
                        if any(reg in rt for rt in _reach):
                            _hit = pt
                            break
                    if _hit:
                        break
            if _hit:
                score += 3
                reasons.append(f"Distributes in your production market: {_hit.title()}")

        # --- Festival scouting linkage — the strongest real signal ---
        scouted_overlap = set(dist.get("scouts_festivals") or []).intersection(
            set(matched_festival_names)
        )
        if scouted_overlap:
            score += 4.0
            reasons.append(
                f"Actively scouts festivals you're matched to: {', '.join(sorted(scouted_overlap))}"
            )

        # --- Declared Target Audience match — declared-only ---
        dist_aud = {str(a).lower() for a in (dist.get("audience_focus") or [])}
        aud_overlap = declared_audience.intersection(dist_aud)
        if aud_overlap:
            score += 3.0
            reasons.append(
                f"Acquires for the audience you declared: {', '.join(sorted(aud_overlap))}"
            )

        # --- Representation match — STRICT OPT-IN ONLY ---
        dist_rep = dist.get("specialty_representation") or ["general"]
        if dist_rep != ["general"] and "general" not in dist_rep:
            user_rep_signals = set()
            if representation_gender and representation_gender.lower() == "woman":
                user_rep_signals.add("women")
            for m in representation_minority:
                if m and "lgbtq" in m.lower():
                    user_rep_signals.add("lgbtq+")
                if m and ("racial" in m.lower() or "ethnic" in m.lower()):
                    user_rep_signals.add("racial_ethnic_minority")
            rep_overlap = user_rep_signals.intersection(set(dist_rep))
            if rep_overlap:
                score += 3.0
                reasons.append(
                    f"Specialty aligns with your Representation selection: {', '.join(sorted(rep_overlap))}"
                )

        if score > 0:
            if dist.get("budget_tier_fit"):
                reasons.append(f"Budget fit: {dist['budget_tier_fit']}")
            matches.append(DistributorMatch(distributor=dist, score=score, reasons=reasons))

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches


# ---------------------------------------------------------------------------
# Grants matching (handoff code/grants_matcher.py - PRO spec Section 07)
# ---------------------------------------------------------------------------
# Hard gates exclude; additive signals score; nothing is inferred.
#   G1 format, G2 deadline (passed never renders), G3 staleness (flag 4mo /
#   exclude 6mo, unverified excluded), G4 nationality-with-no-route,
#   budget outside stated bounds. Badges: NATIONALITY RESTRICTION /
#   CO-PRODUCTION REQUIRED / CLOSING SOON / LEGISLATIVE RISK.

GRANT_FORMAT_MAP = {
    "feature film": "feature",
    "feature": "feature",
    "short": "short",
    "short film": "short",
    "tv series": "tv_series",
    "tv pilot": "tv_series",
    "limited series": "tv_series",
    "mini-series": "tv_series",
    "documentary": "documentary",
    "docuseries": "documentary",
    "animated feature": "animation",
    "animation series": "animation",
}

STALE_EXCLUDE_MONTHS = 6
STALE_FLAG_MONTHS = 4
CLOSING_SOON_DAYS = 56


def _parse_date(s):
    if not s:
        return None
    s = str(s).strip()[:10]
    try:
        parts = s.split("-")
        if len(parts) == 3:
            return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, TypeError):
        return None
    return None


def _months_between(d1: date, d2: date) -> float:
    return (d2.year - d1.year) * 12 + (d2.month - d1.month) + (d2.day - d1.day) / 30.0


def match_grants(grants: list[dict], production: dict, today: date | None = None):
    """production: format, genres[], budget_usd, home_country,
    ranked_territories[], script_origin. Returns (matches, admin_flags)."""
    today = today or date.today()
    fmt = GRANT_FORMAT_MAP.get((production.get("format") or "").lower())
    genres = {g.lower() for g in production.get("genres", [])}
    ranked = {t.lower() for t in production.get("ranked_territories", [])}
    origin = (production.get("script_origin") or "").lower() or None
    home = (production.get("home_country") or "").lower()
    budget = production.get("budget_usd")
    loc_continents: set[str] = set()

    matches, flags = [], []

    for g in grants:
        if not g:
            continue
        name = g.get("fund_name") or g.get("title") or ""
        terr = (g.get("territory") or "").lower()
        cont = g.get("continent") or ""

        # ---- G1 format ----
        elig = [str(f).lower() for f in (g.get("eligible_formats") or [])]
        if fmt and elig and fmt not in elig and "all" not in elig:
            continue

        # ---- G2 deadline ----
        deadline_raw = g.get("deadline") or g.get("application_deadline")
        dl_raw = str(deadline_raw or "").lower()
        dl = _parse_date(deadline_raw)
        closing_soon = False
        if dl:
            if dl < today:
                flags.append({"fund_name": name, "flag": "dead_deadline",
                              "detail": f"deadline {deadline_raw} has passed"
                              + (" - annual recurrence, re-date next cycle"
                                 if g.get("recurrence") == "annual" else "")})
                continue
            closing_soon = (dl - today) <= timedelta(days=CLOSING_SOON_DAYS)
        elif dl_raw not in ("rolling", "") and g.get("recurrence") != "rolling":
            flags.append({"fund_name": name, "flag": "deadline_unparseable",
                          "detail": f"deadline field '{deadline_raw}' is not a date or 'rolling'"})

        # ---- G3 staleness ----
        vd = _parse_date(g.get("verified_at") or g.get("last_verified_at"))
        if vd:
            age = _months_between(vd, today)
            if age > STALE_EXCLUDE_MONTHS:
                flags.append({"fund_name": name, "flag": "stale_excluded",
                              "detail": f"verified_at > {STALE_EXCLUDE_MONTHS} months - excluded from paid reports"})
                continue
            if age > STALE_FLAG_MONTHS:
                flags.append({"fund_name": name, "flag": "stale_flag",
                              "detail": f"verified_at > {STALE_FLAG_MONTHS} months - re-verify"})
        else:
            flags.append({"fund_name": name, "flag": "stale_excluded",
                          "detail": "no verified_at - excluded from paid reports until verified"})
            continue

        # ---- G4 nationality ----
        score = 0.0
        signals, badges = [], []
        terr_matches_ranked = terr in ranked
        terr_matches_origin = bool(origin and terr == origin)
        terr_matches_home = bool(home and terr == home)
        if g.get("nationality_required"):
            badges.append("NATIONALITY RESTRICTION")
            if terr_matches_home:
                score += 2
                signals.append(f"nationality requirement met - you are based in {g.get('territory')}")
            elif terr_matches_ranked or terr_matches_origin:
                score -= 1
                signals.append(
                    f"restricted to {g.get('territory')} entities - a local co-production structure would be required"
                )
            else:
                continue  # no route to eligibility: excluded rather than misleadingly listed

        # ---- location signals ----
        if terr_matches_ranked:
            score += 3
            signals.append(f"{g.get('territory')} is a ranked production territory for this project")
        if terr_matches_origin:
            score += 3
            signals.append(f"{g.get('territory')} is where your script is predominantly set")
        if not (terr_matches_ranked or terr_matches_origin):
            if terr == "global":
                score += 0.5
                signals.append("open to productions worldwide")
            elif cont and cont.lower() in loc_continents:
                score += 1
                signals.append(f"open across {cont}, which includes your production territories")

        # ---- genre (weak, capped) ----
        gtags = {str(t).lower() for t in (g.get("genre_tags") or [])}
        if genres and (genres & gtags or "all" in gtags):
            score += 2
            hit = sorted(genres & gtags)
            signals.append("accepts " + (", ".join(hit) if hit else "all genres"))

        # ---- format specificity ----
        if fmt and elig and fmt in elig and len(elig) <= 2:
            score += 1
            signals.append(f"specifically funds {fmt.replace('_', ' ')} projects")

        # ---- budget window ----
        lo, hi = g.get("budget_min_usd"), g.get("budget_max_usd")
        if budget and hi is not None and budget > hi:
            continue  # over the fund's stated ceiling: ineligible
        if budget and lo is not None and budget < lo:
            continue
        if budget and lo is not None and hi is not None and lo <= budget <= hi:
            score += 2
            signals.append("your budget sits inside this fund's stated range")

        # ---- badges ----
        if g.get("co_production_required"):
            badges.append("CO-PRODUCTION REQUIRED")
        if closing_soon:
            badges.append("CLOSING SOON")
        if g.get("legislative_risk"):
            badges.append("LEGISLATIVE RISK")

        if score > 0:
            matches.append({"grant": g, "score": round(score, 1),
                            "signals": signals, "badges": badges})

    matches.sort(key=lambda m: (-m["score"], m["grant"].get("fund_name") or m["grant"].get("title") or ""))
    return matches, flags
