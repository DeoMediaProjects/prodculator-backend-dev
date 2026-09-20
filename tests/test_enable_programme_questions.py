"""Making a programme answerable, and the three things that takes.

A report shows no rebate figure when the producer was never asked for the
statutory base. The wizard asks only when a programme resolves to a
jurisdiction, carries a slug, and has a declared input row — and forty-eight of
fifty-seven failed the first, so most of the catalogue was invisible to the
form that fills `calculation_inputs`.

The last test is the one that matters: after this runs, the same resolver the
wizard uses returns a question for a programme that had none.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.modules.incentives.v2_jurisdictions import resolve_jurisdiction
from app.modules.incentives.v2_question_resolver import resolve_questions
from scripts.enable_programme_questions import apply_plan, plan

PROGRAMMES = """
CREATE TABLE incentive_programs (
    id TEXT PRIMARY KEY,
    programme_id VARCHAR(96),
    program TEXT,
    territory TEXT NOT NULL,
    status TEXT,
    qs_engine_type VARCHAR(32),
    jurisdiction_country VARCHAR(96),
    jurisdiction_subdivision VARCHAR(96)
)
"""
INPUTS = """
CREATE TABLE programme_required_inputs (
    id TEXT PRIMARY KEY,
    programme_id VARCHAR(96),
    rule_version TEXT,
    input_key TEXT,
    label TEXT,
    input_type TEXT,
    required_for_exact BOOLEAN,
    help_text TEXT,
    dependency_rules_json TEXT,
    validation_rules_json TEXT,
    missing_input_behavior TEXT,
    calculation_input_schema_version TEXT,
    created_at TEXT
)
"""


def _database(tmp_path, programmes, inputs=()) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'incentives.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text(PROGRAMMES))
        conn.execute(sa.text(INPUTS))
        for row in programmes:
            conn.execute(
                sa.text(
                    "INSERT INTO incentive_programs "
                    "(id, programme_id, program, territory, status, qs_engine_type, "
                    " jurisdiction_country, jurisdiction_subdivision) VALUES "
                    "(:id, :programme_id, :program, :territory, :status, :engine, "
                    " :country, :subdivision)"
                ),
                {
                    "id": row["id"],
                    "programme_id": row.get("programme_id"),
                    "program": row.get("program"),
                    "territory": row["territory"],
                    "status": row.get("status", "active"),
                    "engine": row.get("qs_engine_type"),
                    "country": row.get("jurisdiction_country"),
                    "subdivision": row.get("jurisdiction_subdivision"),
                },
            )
        for row in inputs:
            conn.execute(
                sa.text(
                    "INSERT INTO programme_required_inputs "
                    "(id, programme_id, input_key, label, help_text) VALUES "
                    "(:id, :programme_id, :input_key, :label, :help_text)"
                ),
                row,
            )
    return engine


def _row(engine, identity: str) -> dict:
    with engine.connect() as conn:
        return dict(conn.execute(
            sa.text("SELECT * FROM incentive_programs WHERE id = :id"),
            {"id": identity},
        ).mappings().one())


def _inputs(engine) -> list[dict]:
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(
            sa.text("SELECT * FROM programme_required_inputs")
        ).mappings()]


_MEXICO = {
    "id": "p1", "program": "EFICA", "territory": "Mexico",
    "qs_engine_type": "ELIGIBLE_LOCAL_SPEND",
}


def test_a_programme_with_no_jurisdiction_gets_all_three(tmp_path):
    engine = _database(tmp_path, [_MEXICO])
    result = plan(engine)
    assert result.is_clean
    apply_plan(engine, result)

    row = _row(engine, "p1")
    assert row["jurisdiction_country"] == "MX"
    assert row["jurisdiction_subdivision"] is None
    assert row["programme_id"] == "MX_EFICA"
    assert [i["input_key"] for i in _inputs(engine)] == ["eligible_local_spend"]


def test_a_subdivision_programme_resolves_below_the_country(tmp_path):
    engine = _database(tmp_path, [{
        "id": "p1", "program": "New York State Film Tax Credit",
        "territory": "New York", "qs_engine_type": "ELIGIBLE_LOCAL_SPEND",
    }])
    apply_plan(engine, plan(engine))
    row = _row(engine, "p1")
    assert row["jurisdiction_country"] == "US"
    assert row["jurisdiction_subdivision"] == "US-NY"
    assert row["programme_id"].startswith("US_NY_")


def test_hand_written_wording_is_never_overwritten(tmp_path):
    """The nine that work today carry help text a generated label would lose."""
    engine = _database(
        tmp_path,
        [{
            "id": "p1", "programme_id": "GB_AVEC", "program": "AVEC",
            "territory": "United Kingdom", "qs_engine_type": "CORE_LOWER_OF",
            "jurisdiction_country": "GB",
        }],
        [
            {"id": "GB_AVEC:local_core_expenditure", "programme_id": "GB_AVEC",
             "input_key": "local_core_expenditure", "label": "UK core expenditure",
             "help_text": "The portion of core costs incurred in the UK."},
            {"id": "GB_AVEC:global_core_expenditure", "programme_id": "GB_AVEC",
             "input_key": "global_core_expenditure", "label": "Relevant global core",
             "help_text": "All core costs wherever incurred."},
        ],
    )
    result = plan(engine)
    assert result.questions == [] and result.already_answerable == 1
    apply_plan(engine, result)
    assert {i["label"] for i in _inputs(engine)} == {
        "UK core expenditure", "Relevant global core",
    }
    assert _row(engine, "p1")["programme_id"] == "GB_AVEC"


def test_a_partly_declared_programme_gains_only_what_is_missing(tmp_path):
    engine = _database(
        tmp_path,
        [{
            "id": "p1", "programme_id": "GB_AVEC", "program": "AVEC",
            "territory": "United Kingdom", "qs_engine_type": "CORE_LOWER_OF",
            "jurisdiction_country": "GB",
        }],
        [{"id": "GB_AVEC:local_core_expenditure", "programme_id": "GB_AVEC",
          "input_key": "local_core_expenditure", "label": "UK core expenditure",
          "help_text": "Written by a person."}],
    )
    apply_plan(engine, plan(engine))
    by_key = {i["input_key"]: i for i in _inputs(engine)}
    assert set(by_key) == {"local_core_expenditure", "global_core_expenditure"}
    assert by_key["local_core_expenditure"]["help_text"] == "Written by a person."
    assert by_key["global_core_expenditure"]["help_text"] == ""


def test_a_non_spend_engine_is_never_asked_for_a_base(tmp_path):
    """A question implying an investor shelter pays on spend would mislead."""
    engine = _database(tmp_path, [{
        "id": "p1", "program": "EFICINE", "territory": "Mexico",
        "qs_engine_type": "INVESTOR_TAX_SHELTER",
    }])
    result = plan(engine)
    apply_plan(engine, result)
    assert result.questions == []
    assert _inputs(engine) == []
    # It still resolves, so the report can say what it is.
    assert _row(engine, "p1")["jurisdiction_country"] == "MX"


def test_multi_bucket_is_left_for_a_human(tmp_path):
    engine = _database(tmp_path, [{
        "id": "p1", "program": "Illinois Film Tax Credit", "territory": "Illinois",
        "qs_engine_type": "MULTI_BUCKET",
    }])
    result = plan(engine)
    assert result.questions == []
    assert [name for name, _ in result.multi_bucket] == ["Illinois Film Tax Credit"]


def test_an_unresolvable_territory_is_reported_not_guessed(tmp_path):
    engine = _database(tmp_path, [{
        "id": "p1", "program": "Somewhere Credit", "territory": "Atlantis",
        "qs_engine_type": "ELIGIBLE_LOCAL_SPEND",
    }])
    result = plan(engine)
    assert result.unresolved == [("Somewhere Credit", "Atlantis")]
    assert not result.is_clean
    apply_plan(engine, result)
    assert _row(engine, "p1")["jurisdiction_country"] is None


def test_a_suspended_programme_is_left_alone(tmp_path):
    engine = _database(tmp_path, [{
        "id": "p1", "program": "KOFIC Location Incentive", "territory": "South Korea",
        "status": "suspended", "qs_engine_type": "ELIGIBLE_LOCAL_SPEND",
    }])
    result = plan(engine)
    apply_plan(engine, result)
    assert _row(engine, "p1")["jurisdiction_country"] is None
    assert _inputs(engine) == []


def test_a_long_programme_name_still_fits_the_id_column(tmp_path):
    """`programme_required_inputs.id` is varchar(64) and holds slug:input_key."""
    from scripts.enable_programme_questions import MAX_SLUG

    engine = _database(tmp_path, [{
        "id": "p1",
        "program": (
            "SCRI.PT / RIPAC - Financial Incentive for Audiovisual Production "
            "(replaces PIC), Portugal, effective 2026"
        ),
        "territory": "Portugal", "qs_engine_type": "ELIGIBLE_LOCAL_SPEND",
    }])
    apply_plan(engine, plan(engine))
    slug = _row(engine, "p1")["programme_id"]
    assert len(slug) <= MAX_SLUG
    # Filler dropped, so it still reads as a programme rather than a truncation.
    assert slug.startswith("PT_")
    assert "FINANCIAL" in slug
    row = _inputs(engine)[0]
    assert len(row["id"]) <= 64


def test_a_short_name_keeps_its_own_words(tmp_path):
    engine = _database(tmp_path, [{
        "id": "p1", "program": "Film Tax Credit", "territory": "Malta",
        "qs_engine_type": "ELIGIBLE_LOCAL_SPEND",
    }])
    apply_plan(engine, plan(engine))
    assert _row(engine, "p1")["programme_id"] == "MT_FILM_TAX_CREDIT"


def test_two_programmes_colliding_on_a_slug_are_reported(tmp_path):
    """Usually a duplicate worth looking at, not a numbering problem."""
    engine = _database(tmp_path, [
        {"id": "p1", "program": "Film Credit", "territory": "Mexico",
         "qs_engine_type": "ELIGIBLE_LOCAL_SPEND"},
        {"id": "p2", "program": "Film Credit", "territory": "Mexico",
         "qs_engine_type": "ELIGIBLE_LOCAL_SPEND"},
    ])
    result = plan(engine)
    assert len(result.slugs) == 1
    assert len(result.slug_collisions) == 1
    assert not result.is_clean


def test_running_twice_changes_nothing(tmp_path):
    engine = _database(tmp_path, [_MEXICO])
    apply_plan(engine, plan(engine))
    again = plan(engine)
    assert (again.jurisdictions, again.slugs, again.questions) == ([], [], [])
    assert again.already_answerable == 1


def test_the_wizard_then_asks_a_question_it_could_not_ask_before(tmp_path):
    """The point of the whole script, through the resolver the wizard uses."""
    engine = _database(tmp_path, [_MEXICO])
    jurisdiction = resolve_jurisdiction("Mexico")

    def _ask():
        with engine.connect() as conn:
            programmes = [dict(r) for r in conn.execute(
                sa.text("SELECT * FROM incentive_programs")
            ).mappings()]
            declared = [dict(r) for r in conn.execute(
                sa.text("SELECT * FROM programme_required_inputs")
            ).mappings()]
        return resolve_questions(jurisdiction, programmes, declared)

    assert list(_ask().questions) == [], (
        "precondition: Mexico is invisible to the wizard"
    )

    apply_plan(engine, plan(engine))

    asked = _ask()
    assert [q.input_key for q in asked.questions] == ["eligible_local_spend"]
    assert asked.questions[0].label == "Defined local eligible expenditure"
    assert asked.questions[0].used_by == ("EFICA",)
