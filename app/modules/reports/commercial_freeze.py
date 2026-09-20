"""Normalising the frozen commercial workbook's raw labels into engine vocabulary.

WHY A SEPARATE LAYER
--------------------
The 101-company freeze is a faithful read of a reviewed workbook, and its
columns speak the workbook's vocabulary rather than the engine's. ``access_route_status``
holds seven distinct raw strings, ``submission_route`` holds those seven plus
free prose, and ``relationship_type`` holds eight variants of "this company sold
this film". Matching against the raw strings would mean every consumer
re-deriving the same mapping, and the first one to spell a state differently
would silently stop matching.

So the translation happens once, here, and it is the only place that knows what
the workbook's words mean.

THE RULE THAT SHAPES EVERY MAPPING
----------------------------------
The implementation note is explicit that a public contact page is not proof that
unsolicited submissions are accepted. Fifty-six of the 101 companies carry
``PUBLIC_ACQUISITIONS_CONTACT`` or ``PUBLIC_CONTACT_AVAILABLE``, and reading
either as an open door would turn a directory listing into an invitation across
more than half the catalogue.

Both therefore normalise to ``CONTACT_PUBLISHED``: a real, useful fact — there is
a named acquisitions contact — that is not a statement about whether this
production may approach them. ``DIRECT_OPEN`` is the only state that says a
producer may submit directly, and three companies hold it.

The same caution governs roles and relationships. ``SALES_HANDLED`` and
``WORLD_SALES`` are both sales relationships and normalise together;
``DISTRIBUTOR`` does not join them, because a company that distributed a film in
one territory has not thereby acted as its sales agent, and the implementation
note forbids inferring one from the other.
"""

from __future__ import annotations

from typing import Any

# ── Access routes ────────────────────────────────────────────────────────────

#: A producer may submit directly. The only state that says so.
ACCESS_DIRECT = "DIRECT_OPEN"
#: A named acquisitions contact is published. Not permission to use it.
ACCESS_CONTACT_PUBLISHED = "CONTACT_PUBLISHED"
#: Approach must come through an agent, attorney, sales company or a market.
ACCESS_REPRESENTATIVE_ONLY = "REPRESENTATIVE_ONLY"
#: The company states it does not accept unsolicited material.
ACCESS_NO_UNSOLICITED = "NO_UNSOLICITED"
#: Nothing sourced. Never optimism.
ACCESS_UNKNOWN = "ACCESS_ROUTE_UNKNOWN"

_ACCESS_ROUTES: dict[str, str] = {
    "DIRECT_OPEN": ACCESS_DIRECT,
    # A published contact is a directory fact, not an invitation. Both of these
    # raw states cover more than half the catalogue, so reading either as an
    # open route would be the single largest overstatement the engine could make.
    "PUBLIC_ACQUISITIONS_CONTACT": ACCESS_CONTACT_PUBLISHED,
    "PUBLIC_CONTACT_AVAILABLE": ACCESS_CONTACT_PUBLISHED,
    "REPRESENTATIVE_ONLY": ACCESS_REPRESENTATIVE_ONLY,
    "REPRESENTATIVE_OR_MARKET_ROUTE": ACCESS_REPRESENTATIVE_ONLY,
    "UNSOLICITED_NOT_ACCEPTED": ACCESS_NO_UNSOLICITED,
    "DOES_NOT_ACCEPT_UNSOLICITED": ACCESS_NO_UNSOLICITED,
    "DOES_NOT_ACCEPT_UNSOLICITED_SUBMISSIONS": ACCESS_NO_UNSOLICITED,
    "UNKNOWN": ACCESS_UNKNOWN,
}

#: How much each access route is worth in the frozen score's access-path
#: component, as a fraction of that component's weight. A published contact
#: scores partially — it is genuinely easier to reach than a company with
#: nothing sourced — without being treated as an open route.
ACCESS_QUALITY: dict[str, float] = {
    ACCESS_DIRECT: 1.0,
    ACCESS_CONTACT_PUBLISHED: 0.5,
    ACCESS_REPRESENTATIVE_ONLY: 0.5,
    # A stated refusal is a real, sourced fact and scores zero rather than
    # unknown: the producer needs to see it, and it must not rank as if the
    # question were merely open.
    ACCESS_NO_UNSOLICITED: 0.0,
    ACCESS_UNKNOWN: 0.0,
}

#: Reader-facing text. Written for a producer deciding whether to spend a week
#: preparing an approach, not for an engineer reading a state machine.
ACCESS_LABELS: dict[str, str] = {
    ACCESS_DIRECT: "Accepts direct submissions",
    ACCESS_CONTACT_PUBLISHED: (
        "Acquisitions contact published — this is not confirmation that "
        "unsolicited submissions are accepted"
    ),
    ACCESS_REPRESENTATIVE_ONLY: (
        "Approach through a representative, sales company or market only"
    ),
    ACCESS_NO_UNSOLICITED: "States it does not accept unsolicited submissions",
    ACCESS_UNKNOWN: "Access route not established",
}


def normalise_access_route(row: dict[str, Any]) -> str:
    """The canonical access route for one frozen company row.

    Reads ``access_route_status`` first and ``submission_route`` only as a
    fallback, because the former is the reviewed column and the latter still
    holds free prose on a handful of rows. Prose that matches no known state
    yields UNKNOWN rather than a guess: "Acquisitions team exists" tells us a
    department is staffed, not how a producer reaches it.
    """
    columns = ("access_route_status", "submission_route", "unsolicited_policy")

    # A stated refusal in any column outranks anything permissive in another,
    # so it is tested across every column before the ordinary precedence runs.
    # Checking in column order instead would let a published acquisitions
    # contact in the first column mask an explicit "does not accept unsolicited"
    # in the third — the one fact the producer most needs to act on.
    for column in columns:
        raw = str(row.get(column) or "").strip().upper()
        if _ACCESS_ROUTES.get(raw) == ACCESS_NO_UNSOLICITED:
            return ACCESS_NO_UNSOLICITED

    for column in columns:
        raw = str(row.get(column) or "").strip().upper()
        route = _ACCESS_ROUTES.get(raw)
        if route and route != ACCESS_UNKNOWN:
            return route
    return ACCESS_UNKNOWN


# ── Relationship semantics ───────────────────────────────────────────────────

#: This company sold the film — acted as sales agent or world sales.
RELATIONSHIP_SALES = "SALES"
#: This company distributed the film in one or more territories.
RELATIONSHIP_DISTRIBUTION = "DISTRIBUTION"
#: The workbook's label does not settle which.
RELATIONSHIP_UNTYPED = "UNTYPED"

_RELATIONSHIPS: dict[str, str] = {
    "SALES_HANDLED": RELATIONSHIP_SALES,
    "WORLD_SALES": RELATIONSHIP_SALES,
    "SALES": RELATIONSHIP_SALES,
    "DISTRIBUTOR": RELATIONSHIP_DISTRIBUTION,
    "US_DISTRIBUTOR": RELATIONSHIP_DISTRIBUTION,
}

#: Labels naming both activities. Kept as a pair rather than collapsed to one,
#: because collapsing would be the inference the implementation note forbids:
#: a sales relationship is not evidence of a distribution relationship, and the
#: reverse is equally untrue.
_COMPOUND_RELATIONSHIPS: dict[str, frozenset[str]] = {
    "WORLD_SALES_DISTRIBUTION": frozenset(
        {RELATIONSHIP_SALES, RELATIONSHIP_DISTRIBUTION}
    ),
    "SALES_DISTRIBUTION": frozenset({RELATIONSHIP_SALES, RELATIONSHIP_DISTRIBUTION}),
    # Production is not a commercial route and contributes nothing here, so this
    # carries only its sales limb.
    "WORLD_SALES_PRODUCTION": frozenset({RELATIONSHIP_SALES}),
}


def normalise_relationship_type(raw: Any) -> frozenset[str]:
    """Which commercial activities one frozen relationship row evidences.

    Returns a set because a label may name both. An unrecognised label yields
    ``{UNTYPED}`` rather than a default: a relationship whose nature the source
    does not state is evidence that the company handled the film somehow, and
    nothing more precise than that.
    """
    key = str(raw or "").strip().upper()
    if not key:
        return frozenset({RELATIONSHIP_UNTYPED})
    if key in _COMPOUND_RELATIONSHIPS:
        return _COMPOUND_RELATIONSHIPS[key]
    single = _RELATIONSHIPS.get(key)
    return frozenset({single}) if single else frozenset({RELATIONSHIP_UNTYPED})


# ── Group identity ───────────────────────────────────────────────────────────


def portfolio_group(row: dict[str, Any]) -> str:
    """The key that decides whether two companies compete for one package slot.

    A recommendation list holding a parent and its own label twice has spent two
    of a producer's five slots on one commercial route. The freeze already
    carries ``portfolio_group_key`` for this and groups three pairs under it; it
    is preferred, with the canonical company ID and then the company's own ID as
    fallbacks so a row missing the key still groups with itself rather than with
    every other row missing it.
    """
    for column in ("portfolio_group_key", "canonical_company_id", "company_id"):
        value = str(row.get(column) or "").strip()
        if value:
            return value.upper()
    return str(row.get("company_name") or "").strip().upper()


def is_current_brand(row: dict[str, Any]) -> bool:
    """Whether this row names a company operating under its own name today.

    ``LEGACY_SUCCESSOR_ROUTE`` means the brand no longer trades and approaches go
    to a successor. Recommending it by name would send a producer to a company
    that does not exist.
    """
    return str(row.get("brand_status") or "").strip().upper() != "LEGACY_SUCCESSOR_ROUTE"


# ── Roles ────────────────────────────────────────────────────────────────────

#: The engine recognises two commercial roles, and the freeze records six. The
#: mapping is not a simplification — it is the answer to a narrower question.
#: ``SALES_FINANCE_PRODUCTION`` describes a company that also finances and
#: produces, and neither of those is a route to market, so it contributes its
#: sales limb and nothing else. Reading it as a third role would put financing
#: companies into a list a producer reads as "who might sell this".
_COMPANY_ROLES: dict[str, tuple[str, ...]] = {
    "INTERNATIONAL_SALES": ("sales_agent",),
    "WORLD_SALES_AGENT": ("sales_agent",),
    "SALES_FINANCE_PRODUCTION": ("sales_agent",),
    "TERRITORIAL_DISTRIBUTOR": ("distributor",),
    "TERRITORIAL_DISTRIBUTOR_ACQUIRER": ("distributor",),
    "STUDIO_SPECIALTY_DISTRIBUTOR": ("distributor",),
    "STREAMER_DISTRIBUTOR": ("distributor",),
    "DISTRIBUTOR_STUDIO": ("distributor",),
    "HYBRID_SALES_DISTRIBUTION": ("sales_agent", "distributor"),
}


def commercial_roles(row: dict[str, Any]) -> tuple[str, ...]:
    """Which of the engine's two roles one frozen company row evidences.

    Reads ``company_roles``, the reviewed semicolon-separated column, and falls
    back to ``company_type``. An unrecognised token contributes nothing rather
    than defaulting: a company whose role nobody established is not thereby a
    sales agent, and ``match_company`` already declines a profile with no role.
    """
    roles: set[str] = set()
    for column in ("company_roles", "company_type"):
        for token in str(row.get(column) or "").split(";"):
            roles.update(_COMPANY_ROLES.get(token.strip().upper(), ()))
        if roles:
            break
    return tuple(sorted(roles))


# ── Scope phrases ────────────────────────────────────────────────────────────
#
# WHY TWO LOOKUP TABLES AND NOT A PARSER
# --------------------------------------
# ``territory_scope`` and ``formats`` are prose, and the freeze's own note says
# so: import and matching require a separate field-level review. A parser would
# be that review performed by nobody — it would read "Worldwide / major
# territories" as worldwide, and the producer would be told a company holds
# rights it does not.
#
# So the review is the table. Every distinct phrase the pinned snapshot contains
# appears below exactly once, and each was read and decided. An empty tuple is
# not a gap; it records that the phrase was read and found to say nothing the
# engine can test. A phrase absent from the table is a different thing entirely
# — the snapshot changed without anyone looking — and the importer stops.
#
# Lookup is on the whole string, whitespace-collapsed and casefolded. Never a
# prefix, never a substring: substring matching is what turns "Worldwide /
# major territories" into "Worldwide" again by a different route.
#
# WHERE A HEDGE APPEARS
# ---------------------
# Several phrases name definite formats and then hedge one of them — "Feature
# Film; Documentary; Animation where stated". The hedged limb is dropped and the
# definite ones are kept, which under-claims rather than over-claims. That
# direction is safe here: a format the company does handle but we did not record
# leaves the gate UNKNOWN, while a format it does not handle would read as PASS.

_WORLDWIDE = ("worldwide",)

#: Every ``territory_scope`` phrase in the pinned freeze, and the rights scope
#: it states. Eighty of the 101 rows carry a phrase that types; the rest name a
#: home territory qualified by prose ("primarily", "ambition", "via <another
#: company>") that says where a company is active without stating what rights
#: it takes.
_RIGHTS_TERRITORY_PHRASES: dict[str, tuple[str, ...]] = {
    "worldwide": _WORLDWIDE,
    "worldwide / international": _WORLDWIDE,
    "worldwide incl. north america": _WORLDWIDE,
    "international / worldwide sales": _WORLDWIDE,
    "worldwide sales + uk theatrical": _WORLDWIDE,
    "global / country-specific theatrical and streaming operations": _WORLDWIDE,
    "united states": ("united states",),
    "canada": ("canada",),
    "north america": ("north america",),
    "north american marketplace": ("north america",),
    "united states / north america": ("united states", "north america"),
    "australia & new zealand": ("australia", "new zealand"),
    # Read and found untypable. "Major territories" contradicts worldwide, a
    # country count is not a territory, and "international" without a home
    # territory does not say which side of the line the company sits on.
    "worldwide / major territories": (),
    "70+ countries / global platforms": (),
    "international": (),
    "international sales + uk distribution": (),
    "united kingdom + international sales": (),
    "united states / global activity": (),
    "united states / global studio network": (),
    "united states / world-market activity": (),
    "primarily united states / north america; independent films worldwide origin": (),
    "north america; international marketplace": (),
    "north america; international sales arm also active": (),
    "canada; international": (),
    "canada; international marketplace": (),
    "canada; site states canadian marketplace and abroad": (),
    "canada; quebec via métropole films": (),
    "australia & new zealand; world sales for selected australian catalogue/titles": (),
    "africa; platform/client network": (),
    "nigeria; anglophone west africa; global distribution of african content": (),
    "south africa; rest of african continent": (),
    "west africa / pan-african ambition": (),
    "uk & ireland distribution; global presence; international sales via capture": (),
}

#: Every ``formats`` phrase in the pinned freeze, in the canonical format
#: vocabulary of ``app.core.formats``. Limbs naming another company's label
#: ("via Omnibus label", "via group services") are dropped: that is where the
#: format goes, not what this company acquires.
_ACQUISITION_FORMAT_PHRASES: dict[str, tuple[str, ...]] = {
    "feature films": ("feature",),
    "feature films; catalogue": ("feature",),
    "feature films; classics": ("feature",),
    "feature films; family/animation/genre/art-house represented": ("feature",),
    "feature; documentary": ("documentary", "feature"),
    "feature films; documentary": ("documentary", "feature"),
    "documentary features": ("documentary", "feature"),
    "documentary features; documentary series where slate supports": ("documentary", "feature"),
    "feature; documentary where slate supports": ("feature",),
    "feature; documentary; animation": ("animation", "documentary", "feature"),
    "feature film; documentary; animation where stated": ("documentary", "feature"),
    "feature; documentary; animation where slate supports": ("documentary", "feature"),
    "feature; animation; other independent cinema": ("animation", "feature"),
    "feature; documentary; international film": ("documentary", "feature"),
    "feature; documentary; other cinema formats where curated": ("documentary", "feature"),
    "feature; documentary; international/arthouse; series via group services": (
        "documentary", "feature",
    ),
    "feature; documentary; tv/other via omnibus label": ("documentary", "feature"),
    "feature; documentary; tv content": ("documentary", "feature", "tv_series"),
    "narrative and documentary theatrical features": ("documentary", "feature"),
    "scripted; unscripted; feature; documentary/factual": ("documentary", "feature"),
    "feature; short": ("feature", "short"),
    "feature; television": ("feature", "tv_series"),
    "feature films; television": ("feature", "tv_series"),
    "feature; episodic tv": ("feature", "tv_series"),
    "features; series": ("feature", "tv_series"),
    # Read and found untypable. "Film" does not say feature or short; a list
    # "represented in catalogue" describes a library rather than an acquisition
    # scope; and a completion state is not a format at all.
    "film; television": (),
    "feature; documentary; animation; series; short; vr represented in catalogue": (),
    "completed projects; other formats require verification": (),
}


class UnreviewedPhrase(KeyError):
    """A scope phrase no reviewer has decided.

    Raised rather than defaulted to unknown. The snapshot is content-pinned, so
    a phrase missing from the table means the pin moved and a scope nobody read
    is about to be imported — which is the one thing these tables exist to stop.
    """


def _phrase(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _typed_scope(
    row: dict[str, Any], column: str, table: dict[str, tuple[str, ...]]
) -> tuple[str, ...]:
    key = _phrase(row.get(column))
    if not key:
        return ()
    if key not in table:
        raise UnreviewedPhrase(
            f"{column} phrase has not been reviewed: {key!r} "
            f"({row.get('company_id') or row.get('company_name')})"
        )
    return table[key]


def rights_territories(row: dict[str, Any]) -> tuple[str, ...]:
    """The rights scope one frozen company row states, or nothing."""
    return _typed_scope(row, "territory_scope", _RIGHTS_TERRITORY_PHRASES)


def acquisition_formats(row: dict[str, Any]) -> tuple[str, ...]:
    """The formats one frozen company row states it acquires, or nothing."""
    return _typed_scope(row, "formats", _ACQUISITION_FORMAT_PHRASES)
