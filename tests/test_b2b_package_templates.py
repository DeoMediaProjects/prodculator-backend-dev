"""Tests for saved package templates (SOW 4.4: "save as template").

The interesting constraint is the name collision rule: templates are picked by
name in the composer, so upserting on name would let one admin silently
overwrite a colleague's template. A collision with a DIFFERENT id is refused.
"""
from unittest.mock import MagicMock

import pytest

from app.modules.b2b.composer_service import (
    PackageTemplateService,
    TemplateNameConflict,
)

KEYS = ["ctx_incentives", "sig_camera", "sig_format"]


def _service(rows=None):
    """rows is the table's contents; lookups are resolved against it by field."""
    store = list(rows or [])
    db = MagicMock()

    def _select(*_a, **_kw):
        sel = MagicMock()
        sel.execute.return_value.data = list(store)

        def _eq(field, value):
            eq = MagicMock()
            eq.execute.return_value.data = [r for r in store if r.get(field) == value]
            return eq

        sel.eq.side_effect = _eq
        return sel

    db.table.return_value.select.side_effect = _select
    db.table.return_value.insert.return_value.execute.return_value.data = None
    db.table.return_value.update.return_value.eq.return_value.execute.return_value.data = None
    db.table.return_value.delete.return_value.eq.return_value.execute.return_value.data = None
    return PackageTemplateService(db), db


def _tpl(tid="tpl-1", name="Quarterly Studio Pack", keys=None):
    return {
        "id": tid,
        "name": name,
        "description": None,
        "section_keys": list(keys or KEYS),
        "product_type": None,
    }


class TestSave:
    def test_creates_with_generated_id_and_timestamps(self):
        svc, db = _service()
        out = svc.save(name="New Pack", section_keys=KEYS, created_by="admin@x.com")

        assert out["name"] == "New Pack"
        assert out["section_keys"] == KEYS
        db.table.return_value.insert.assert_called_once()
        written = db.table.return_value.insert.call_args[0][0]
        # Reflected tables carry no Python-side defaults, so both must be explicit.
        assert written["created_at"] is not None
        assert written["updated_at"] is not None
        assert written["created_by"] == "admin@x.com"

    def test_section_order_is_preserved(self):
        """Order is the render order, so it must survive a round trip."""
        svc, _ = _service()
        reversed_keys = list(reversed(KEYS))
        out = svc.save(name="Ordered", section_keys=reversed_keys)
        assert out["section_keys"] == reversed_keys

    def test_name_collision_with_different_template_is_refused(self):
        svc, _ = _service([_tpl(tid="tpl-1", name="Taken")])
        with pytest.raises(TemplateNameConflict):
            svc.save(name="Taken", section_keys=KEYS)

    def test_updating_a_template_may_keep_its_own_name(self):
        svc, db = _service([_tpl(tid="tpl-1", name="Mine")])
        svc.save(name="Mine", section_keys=KEYS, template_id="tpl-1")
        # Updated in place, not inserted as a duplicate.
        db.table.return_value.update.assert_called_once()
        db.table.return_value.insert.assert_not_called()

    def test_update_does_not_rewrite_created_at(self):
        svc, db = _service([_tpl(tid="tpl-1", name="Mine")])
        svc.save(name="Mine", section_keys=KEYS, template_id="tpl-1")
        written = db.table.return_value.update.call_args[0][0]
        assert "created_at" not in written
        assert written["updated_at"] is not None

    def test_blank_name_rejected(self):
        svc, _ = _service()
        with pytest.raises(ValueError):
            svc.save(name="   ", section_keys=KEYS)

    def test_name_is_trimmed(self):
        svc, _ = _service()
        assert svc.save(name="  Padded  ", section_keys=KEYS)["name"] == "Padded"

    def test_empty_section_list_rejected(self):
        """An empty template would render an empty PDF, so it is refused."""
        svc, _ = _service()
        with pytest.raises(ValueError):
            svc.save(name="Empty", section_keys=[])

    def test_unknown_template_id_rejected(self):
        svc, _ = _service()
        with pytest.raises(ValueError):
            svc.save(name="Ghost", section_keys=KEYS, template_id="missing")


class TestReads:
    def test_list_is_alphabetical_case_insensitive(self):
        svc, _ = _service([
            _tpl("1", "zulu"), _tpl("2", "Alpha"), _tpl("3", "mike"),
        ])
        assert [t["name"] for t in svc.list_all()] == ["Alpha", "mike", "zulu"]

    def test_list_filters_by_product_type(self):
        camera = {**_tpl("1", "Camera Pack"), "product_type": "camera_equipment"}
        crew = {**_tpl("2", "Crew Pack"), "product_type": "crew_casting"}
        svc, _ = _service([camera, crew])

        assert [t["name"] for t in svc.list_all("camera_equipment")] == ["Camera Pack"]
        # Unfiltered returns both.
        assert len(svc.list_all()) == 2

    def test_get_by_name_finds_exact(self):
        svc, _ = _service([_tpl("1", "Exact")])
        assert svc.get_by_name("Exact")["id"] == "1"
        assert svc.get_by_name("Nope") is None


class TestDelete:
    def test_deletes_existing(self):
        svc, db = _service([_tpl("tpl-1")])
        assert svc.delete("tpl-1") is True
        db.table.return_value.delete.assert_called_once()

    def test_missing_returns_false_without_deleting(self):
        svc, db = _service()
        assert svc.delete("nope") is False
        db.table.return_value.delete.assert_not_called()
