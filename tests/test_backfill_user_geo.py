import scripts.backfill_user_geo as bf
from tests.admin_fakes import FakeSupabase


def test_backfill_updates_only_users_missing_country(monkeypatch):
    fake = FakeSupabase(
        {
            "subscriptions": [
                {"user_id": "u1", "stripe_customer_id": "cus_1"},
                {"user_id": "u2", "stripe_customer_id": "cus_2"},
                {"user_id": "u3", "stripe_customer_id": None},
            ],
            "users": [
                {"id": "u1", "country": None},   # eligible
                {"id": "u2", "country": "GB"},   # already set -> skip
                {"id": "u3", "country": None},   # no customer -> skip
            ],
        }
    )
    geo = {"cus_1": ("US", "CA"), "cus_2": ("GB", None)}
    monkeypatch.setattr(bf, "geo_for_customer", lambda cid: geo.get(cid, (None, None)))

    result = bf.backfill_user_geo(fake)
    assert result == {"candidates": 1, "updated": 1}
    u1 = next(u for u in fake.store["users"] if u["id"] == "u1")
    assert u1["country"] == "US"
    assert u1["state"] == "CA"


def test_backfill_dry_run_does_not_write(monkeypatch):
    fake = FakeSupabase(
        {
            "subscriptions": [{"user_id": "u1", "stripe_customer_id": "cus_1"}],
            "users": [{"id": "u1", "country": None}],
        }
    )
    monkeypatch.setattr(bf, "geo_for_customer", lambda cid: ("US", "CA"))
    result = bf.backfill_user_geo(fake, dry_run=True)
    assert result == {"candidates": 1, "updated": 1}
    assert next(u for u in fake.store["users"] if u["id"] == "u1").get("country") is None


def test_geo_for_customer_reads_customer_address(monkeypatch):
    class FakeStripe:
        class Customer:
            @staticmethod
            def retrieve(cid):
                return {"address": {"country": "US", "state": "NY"}}

    monkeypatch.setattr(bf, "stripe", FakeStripe)
    assert bf.geo_for_customer("cus_x") == ("US", "NY")


def test_geo_for_customer_falls_back_to_charge(monkeypatch):
    class FakeChargeList:
        data = [{"billing_details": {"address": {"country": "CA", "state": "BC"}}}]

    class FakeStripe:
        class Customer:
            @staticmethod
            def retrieve(cid):
                return {"address": {}}

        class Charge:
            @staticmethod
            def list(customer, limit):
                return FakeChargeList

    monkeypatch.setattr(bf, "stripe", FakeStripe)
    assert bf.geo_for_customer("cus_y") == ("CA", "BC")
