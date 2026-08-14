"""Issue 2: one stacking answer per programme pair, from every field that states it.

The Tax Incentive Analysis printed both of these about the same pair:

    "SUPPLEMENTARY: UK VFX Expenditure Credit (Uplift) stacks ON TOP of
     AVEC (Enhanced/IFTC)."
    "NARROW ELIGIBILITY -- ... Cannot be combined with the VFX uplift or
     animation uplift."

The detector that should have prevented it missed twice over: it read the exclusion
off the supplementary row while the UK records it on the primary row, and it matched
only "cannot be combined with" while the VFX row says "Cannot combine with".

The correct answer is that they do NOT stack, so the DB prose was right and the
generated note was wrong.
"""
from __future__ import annotations

from app.modules.reports.stacking import (
    MUTUALLY_EXCLUSIVE,
    STACKS,
    UNKNOWN,
    resolve_stacking,
    statements_contradict,
)

# Shapes taken from the live rows, so these tests fail if the real dataset regresses.
UK_IFTC = {
    "program": "AVEC (Enhanced/IFTC)",
    "is_supplementary": False,
    "eligibility_notes": (
        "NARROW ELIGIBILITY -- Independent Film Tax Credit (Enhanced AVEC): 53% gross "
        "/ 39.75% net, but ONLY for films with core production expenditure under "
        "GBP15 million. Requires a separate BFI accreditation beyond the standard "
        "cultural test. Cannot be combined with the VFX uplift or animation uplift. "
        "This is a CONDITIONAL/BONUS programme, not the general UK rate."
    ),
}

UK_AVEC_STANDARD = {
    "program": "UK Audio-Visual Expenditure Credit (AVEC)",
    "is_supplementary": False,
    "eligibility_notes": "Standard rate for audiovisual production.",
}

UK_VFX = {
    "program": "UK VFX Expenditure Credit (Uplift)",
    "is_supplementary": True,
    "eligibility_notes": "Claimed alongside primary credit via corporation tax return.",
    "qs_basis": "Cannot combine with the IFTC enhanced rate or animation uplift",
    "warnings_json": [
        "SUPPLEMENTARY — applies ONLY to qualifying VFX spend, not total budget",
        "Must be claimed alongside IFTC or AVEC — cannot be claimed alone",
    ],
}


class TestTheReportedContradiction:
    def test_vfx_does_not_stack_with_enhanced_iftc(self):
        """The exact pair the report got wrong."""
        result = resolve_stacking(UK_IFTC, UK_VFX)
        assert result["relationship"] == MUTUALLY_EXCLUSIVE
        assert result["stacks"] is False

    def test_the_note_says_it_cannot_be_combined(self):
        note = resolve_stacking(UK_IFTC, UK_VFX)["note"]
        assert "MUTUAL EXCLUSIVITY" in note
        assert "stacks ON TOP of" not in note

    def test_the_exclusion_is_found_on_the_primary_row(self):
        """Reason 1 the old detector missed: it only read the supplementary row.

        With the supplementary row's own constraint text removed, the primary row alone
        must still settle it.
        """
        vfx_without_own_constraint = {
            "program": "UK VFX Expenditure Credit (Uplift)",
            "is_supplementary": True,
            "eligibility_notes": "Claimed alongside primary credit.",
        }
        result = resolve_stacking(UK_IFTC, vfx_without_own_constraint)
        assert result["relationship"] == MUTUALLY_EXCLUSIVE

    def test_cannot_combine_with_phrasing_is_matched(self):
        """Reason 2 it missed: "Cannot combine with", not "cannot be combined with"."""
        iftc_without_exclusion = {
            "program": "AVEC (Enhanced/IFTC)",
            "is_supplementary": False,
            "eligibility_notes": "Enhanced rate for independent film.",
        }
        result = resolve_stacking(iftc_without_exclusion, UK_VFX)
        assert result["relationship"] == MUTUALLY_EXCLUSIVE


class TestLegitimateStacking:
    def test_vfx_stacks_with_standard_avec(self):
        """Both facts are true at once, which is why the check must be pair-aware.

        The VFX uplift genuinely stacks with standard AVEC and genuinely does not stack
        with the enhanced/IFTC rate.
        """
        result = resolve_stacking(UK_AVEC_STANDARD, UK_VFX)
        assert result["relationship"] == STACKS
        assert result["stacks"] is True
        assert "stacks ON TOP of" in result["note"]

    def test_a_supplementary_row_with_no_constraint_stacks_but_says_so(self):
        primary = {"program": "Some Primary Credit", "is_supplementary": False}
        supplementary = {"program": "Some Specialist Uplift", "is_supplementary": True}
        result = resolve_stacking(primary, supplementary)
        assert result["relationship"] == STACKS
        assert "not stated in its terms" in result["note"]


class TestGenericTokensDoNotFalseMatch:
    def test_the_word_credit_alone_does_not_imply_exclusion(self):
        """Without generic-token filtering, "Credit" matches nearly every record."""
        primary = {
            "program": "Georgia Film Tax Credit",
            "is_supplementary": False,
            "eligibility_notes": "Cannot be combined with the Alabama Tax Credit.",
        }
        supplementary = {
            "program": "Georgia Postproduction Credit",
            "is_supplementary": True,
        }
        assert resolve_stacking(primary, supplementary)["relationship"] == STACKS

    def test_an_unrelated_programme_exclusion_is_ignored(self):
        primary = {
            "program": "AVEC (Enhanced/IFTC)",
            "is_supplementary": False,
            "eligibility_notes": "Cannot be combined with the animation uplift.",
        }
        supplementary = {
            "program": "Regional Location Rebate",
            "is_supplementary": True,
        }
        assert resolve_stacking(primary, supplementary)["relationship"] == STACKS


class TestExclusionBeatsPermission:
    def test_contradictory_dataset_resolves_to_not_combining(self):
        """A dataset stating both has a curation problem. Refusing to combine is the
        direction that cannot overstate what a production can claim."""
        primary = {
            "program": "Primary Credit",
            "is_supplementary": False,
            "eligibility_notes": "Cannot be combined with the Specialist Uplift.",
        }
        supplementary = {
            "program": "Specialist Uplift",
            "is_supplementary": True,
            "eligibility_notes": "Stacks on top of Primary Credit.",
        }
        assert resolve_stacking(primary, supplementary)["relationship"] == MUTUALLY_EXCLUSIVE


class TestUnknown:
    def test_a_non_supplementary_pair_with_no_constraint_is_unknown(self):
        result = resolve_stacking(
            {"program": "Programme A"}, {"program": "Programme B"},
        )
        assert result["relationship"] == UNKNOWN
        assert "not stated in either programme's terms" in result["note"]

    def test_missing_rows_do_not_raise(self):
        assert resolve_stacking(None, None)["relationship"] == UNKNOWN


class TestStatementsContradict:
    def test_both_claim_shapes_together_is_a_contradiction(self):
        assert statements_contradict([
            "SUPPLEMENTARY: X stacks ON TOP of Y.",
            "Cannot be combined with the VFX uplift.",
        ]) is True

    def test_one_direction_only_is_not_a_contradiction(self):
        assert statements_contradict(["SUPPLEMENTARY: X stacks ON TOP of Y."]) is False
        assert statements_contradict(["Cannot be combined with Y."]) is False

    def test_empty_and_none_are_safe(self):
        assert statements_contradict([]) is False
        assert statements_contradict([None, ""]) is False
