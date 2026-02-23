from app.core.dependencies import get_current_user, get_supabase


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table_name: str, db):
        self.table_name = table_name
        self.db = db
        self.filters = {}
        self._gte = None
        self._lte = None
        self._delete = False
        self._upsert_payload = None

    def select(self, _value):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def gte(self, key, value):
        self._gte = (key, value)
        return self

    def lte(self, key, value):
        self._lte = (key, value)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _value):
        return self

    def delete(self):
        self._delete = True
        return self

    def upsert(self, payload, on_conflict=None):
        self._upsert_payload = payload
        return self

    def execute(self):
        rows = self.db[self.table_name]
        filtered = [
            row
            for row in rows
            if all(row.get(k) == v for k, v in self.filters.items())
        ]

        if self._gte:
            key, value = self._gte
            filtered = [r for r in filtered if (r.get(key) or "") >= value]
        if self._lte:
            key, value = self._lte
            filtered = [r for r in filtered if (r.get(key) or "") <= value]

        if self._delete:
            self.db[self.table_name] = [r for r in rows if r not in filtered]
            return FakeResult([])

        if self._upsert_payload is not None:
            user_id = self._upsert_payload.get("user_id")
            territory = self._upsert_payload.get("territory")
            exists = any(
                r.get("user_id") == user_id and r.get("territory") == territory for r in rows
            )
            if not exists:
                rows.append(self._upsert_payload)
            return FakeResult([self._upsert_payload])

        return FakeResult(filtered)


class FakeSupabase:
    def __init__(self):
        self.db = {
            "grant_opportunities": [
                {"id": "g1", "title": "Grant 1", "territory": "Canada"},
                {"id": "g2", "title": "Grant 2", "territory": "UK"},
            ],
            "film_festivals": [
                {"id": "f1", "name": "Festival 1", "location": "Toronto"},
            ],
            "territory_watchlist": [],
            "subscriptions": [
                {
                    "id": "s1",
                    "user_id": "user-1",
                    "status": "active",
                    "report_limit": 2,
                    "current_period_start": "2026-01-01T00:00:00Z",
                    "current_period_end": "2026-12-31T00:00:00Z",
                }
            ],
            "reports": [
                {"id": "r1", "user_id": "user-1", "created_at": "2026-02-01T00:00:00Z"},
            ],
        }

    def table(self, table_name: str):
        return FakeQuery(table_name, self.db)


def test_grants_and_festivals_endpoints(client):
    fake_supabase = FakeSupabase()
    client.app.dependency_overrides[get_supabase] = lambda: fake_supabase

    grants_response = client.get("/api/grants?territory=Canada")
    assert grants_response.status_code == 200
    assert len(grants_response.json()) == 1

    festivals_response = client.get("/api/festivals")
    assert festivals_response.status_code == 200
    assert festivals_response.json()[0]["id"] == "f1"


def test_watchlist_crud(client, auth_user):
    fake_supabase = FakeSupabase()
    client.app.dependency_overrides[get_supabase] = lambda: fake_supabase
    client.app.dependency_overrides[get_current_user] = lambda: auth_user

    add_response = client.post(
        "/api/watchlist",
        headers={"Authorization": "Bearer token"},
        json={"territory": "Canada"},
    )
    assert add_response.status_code == 200

    get_response = client.get("/api/watchlist", headers={"Authorization": "Bearer token"})
    assert get_response.status_code == 200
    assert get_response.json()["territories"] == ["Canada"]

    delete_response = client.delete(
        "/api/watchlist?territory=Canada",
        headers={"Authorization": "Bearer token"},
    )
    assert delete_response.status_code == 200


def test_subscription_active_and_can_generate(client, auth_user):
    fake_supabase = FakeSupabase()
    client.app.dependency_overrides[get_supabase] = lambda: fake_supabase
    client.app.dependency_overrides[get_current_user] = lambda: auth_user

    active_response = client.get(
        "/api/subscriptions/active",
        headers={"Authorization": "Bearer token"},
    )
    assert active_response.status_code == 200
    assert active_response.json()["subscription"]["id"] == "s1"

    eligibility_response = client.get(
        "/api/subscriptions/can-generate",
        headers={"Authorization": "Bearer token"},
    )
    assert eligibility_response.status_code == 200
    assert eligibility_response.json()["can_generate"] is True
