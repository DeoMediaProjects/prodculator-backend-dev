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
