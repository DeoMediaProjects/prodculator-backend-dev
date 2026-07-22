"""Functional verification of the B2B v2 signal write + package preview.

Runs the REAL service code against an in-memory fake DB that mimics the query-builder
chain used across the codebase (.table().select().eq().gte().lte().execute() etc.).
No network, no Postgres — proves the logic, not the transport.
"""
import os
import sys
from datetime import date

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-at-least-32-characters-long")
sys.path.insert(0, ".")
import _stub_boot  # noqa: F401,E402  (stubs heavy optional deps)


class FakeResult:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class FakeQuery:
    def __init__(self, table):
        self.t = table
        self._filters = []
        self._head = False
        self._count = False
        self._single = False
        self._pending = None  # ("insert"|"update"|"delete", payload)
        self._limit = None

    def select(self, *a, **k):
        if k.get("count") == "exact":
            self._count = True
        if k.get("head"):
            self._head = True
        return self

    def eq(self, f, v): self._filters.append(("eq", f, v)); return self
    def gte(self, f, v): self._filters.append(("gte", f, v)); return self
    def lte(self, f, v): self._filters.append(("lte", f, v)); return self
    def limit(self, n): self._limit = n; return self
    def single(self): self._single = True; return self

    def insert(self, payload): self._pending = ("insert", payload); return self
    def update(self, payload): self._pending = ("update", payload); return self
    def upsert(self, payload, **k): self._pending = ("upsert", payload); return self
    def delete(self): self._pending = ("delete", None); return self

    def _match(self, row):
        for op, f, v in self._filters:
            rv = row.get(f)
            if op == "eq" and rv != v:
                return False
            if op == "gte" and not (rv is not None and str(rv) >= str(v)):
                return False
            if op == "lte" and not (rv is not None and str(rv) <= str(v)):
                return False
        return True

    def execute(self):
        rows = self.t.rows
        if self._pending:
            kind, payload = self._pending
            if kind in ("insert", "upsert"):
                self.t.rows.append(dict(payload))
                return FakeResult(dict(payload) if self._single else [dict(payload)])
            if kind == "update":
                updated = []
                for r in rows:
                    if self._match(r):
                        r.update(payload)
                        updated.append(dict(r))
                return FakeResult(updated[0] if self._single and updated else updated)
            if kind == "delete":
                keep = [r for r in rows if not self._match(r)]
                removed = len(rows) - len(keep)
                self.t.rows[:] = keep
                return FakeResult([], count=removed)
        matched = [dict(r) for r in rows if self._match(r)]
        if self._count:
            return FakeResult(None, count=len(matched))
        if self._single:
            return FakeResult(matched[0] if matched else None)
        if self._limit:
            matched = matched[: self._limit]
        return FakeResult(matched)


class FakeTable:
    def __init__(self): self.rows = []


class FakeDB:
    def __init__(self): self._tables = {}
    def table(self, name):
        self._tables.setdefault(name, FakeTable())
        return FakeQuery(self._tables[name])


# ---- Stub FXService so no network is needed; NGN->GBP ~ 0.0005 ----
class StubFX:
    def __init__(self, *a, **k): pass
    def convert_budget(self, amount, frm, to="GBP"):
        rates = {"NGN": 0.0005, "USD": 0.79, "EUR": 0.86, "HUF": 0.0022, "GBP": 1.0}
        r = rates.get(frm.upper(), 1.0)
        return {"converted": round(amount * r, 2), "rate": r,
                "rate_date": "2026-07-08", "from_currency": frm, "to_currency": to,
                "display": ""}


import app.modules.reports.service as _rsmod  # noqa: E402
_rsmod.FXService = StubFX  # patch the reference the write path actually uses

from app.modules.reports.service import ReportService  # noqa: E402
from app.modules.b2b.service import B2BService  # noqa: E402
from app.modules.b2b.package_service import PackageService  # noqa: E402

PASS=0; FAIL=0
def check(name, cond):
    global PASS, FAIL
    print(("  PASS " if cond else "  FAIL ")+name)
    if cond: PASS+=1
    else: FAIL+=1


print("=== 1. FX-normalised budget banding (R-1) ===")
db = FakeDB()
rs = ReportService(db)
# A Nigerian feature: NGN 50,000,000 (~£25k) must band as 'micro', NOT high.
report_row = {"id": "scriptA", "created_at": "2026-05-10T00:00:00Z", "report_data": {
    "locationRankings": [{"name": "Lagos"}, {"name": "United Kingdom"}]}}
meta = {"budget_amount": 50_000_000, "budget_currency": "NGN", "b2b_consent": True,
        "production_country": "Nigeria", "territories_considering": ["Lagos", "UK"],
        "format": "Feature Film", "genre": ["Thriller"], "completion_date": "2026-11-01"}
sig = rs.upsert_production_signal(report_id="rep1", report_row=report_row, request_metadata=meta)
check("NGN 50m banded as micro (was 'high' under USD bug)", sig and sig["budget_range"] == "micro")
check("budget stored in GBP (~25000)", sig and 24000 < (sig["budget_amount_gbp"] or 0) < 26000)
check("home_country populated from production_country", sig and sig["home_country"] == "Nigeria")
check("territories_recommended captured from engine output", sig and sig["territories_recommended"] == ["Lagos", "United Kingdom"])
check("format canonicalised 'Feature Film'->'feature'", sig and sig["format"] == "feature")
check("consent stored True", sig and sig["b2b_consent"] is True)

print("=== 2. Consent gate (CRIT-2) ===")
db2 = FakeDB(); rs2 = ReportService(db2)
meta_noconsent = dict(meta); meta_noconsent["b2b_consent"] = False
sig2 = rs2.upsert_production_signal(report_id="rep2", report_row={"id":"scriptB","created_at":"2026-05-10T00:00:00Z"}, request_metadata=meta_noconsent)
check("un-consented signal NOT written", sig2 is None)
check("no row persisted", len(db2._tables.get("production_signals", FakeTable()).rows) == 0)

print("=== 3. Script-level dedupe (Decision 1) ===")
db3 = FakeDB(); rs3 = ReportService(db3)
rr = {"id": "scriptC", "created_at": "2026-05-10T00:00:00Z"}
rs3.upsert_production_signal(report_id="r1", report_row=rr, request_metadata=meta)
rs3.upsert_production_signal(report_id="r2", report_row=rr, request_metadata=meta)  # re-run
rows = db3._tables["production_signals"].rows
check("one row per script after re-run", len(rows) == 1)
check("report_runs incremented to 2", rows[0]["report_runs"] == 2)

print("=== 4. Consent withdrawal removes prior row ===")
meta_withdraw = dict(meta); meta_withdraw["b2b_consent"] = False
rs3.upsert_production_signal(report_id="r3", report_row=rr, request_metadata=meta_withdraw)
check("prior consented row removed on withdrawal", len(db3._tables["production_signals"].rows) == 0)

print("=== 5. Package preview + privacy floors ===")
db4 = FakeDB(); rs4 = ReportService(db4)
# Seed 12 consented UK thrillers (>=10 overall, >=5 per segment) + 2 internal (excluded)
for i in range(12):
    rr_i = {"id": f"s{i}", "created_at": "2026-05-15T00:00:00Z"}
    m = {"budget_amount": 2_000_000, "budget_currency": "GBP", "b2b_consent": True,
         "production_country": "United Kingdom", "format": "Feature Film",
         "genre": ["Thriller"], "territories_considering": ["United Kingdom"]}
    rs4.upsert_production_signal(report_id=f"rep{i}", report_row=rr_i, request_metadata=m)
for i in range(2):
    rr_i = {"id": f"int{i}", "created_at": "2026-05-15T00:00:00Z"}
    m = {"budget_amount": 2_000_000, "budget_currency": "GBP", "b2b_consent": True,
         "is_internal": True, "production_country": "United Kingdom", "format": "Feature Film",
         "genre": ["Thriller"]}
    rs4.upsert_production_signal(report_id=f"repint{i}", report_row=rr_i, request_metadata=m)

b2b = B2BService(db4)
loaded = b2b._load_signals(date(2026,1,1), date(2026,12,31))
check("internal rows excluded from load (R-9)", len(loaded) == 12)
pkg = PackageService(b2b)
prev = pkg.preview(section_keys=["sig_territory_home","sig_genre","sig_budget","sig_crew"],
                   period_start=date(2026,1,1), period_end=date(2026,12,31))
check("overall threshold met (12>=10)", prev["overall_threshold_met"] is True)
terr = next(s for s in prev["sections"] if s["key"]=="sig_territory_home")
check("territory section renderable (12 UK >=5)", terr["renderable"] is True)
crew = next(s for s in prev["sections"] if s["key"]=="sig_crew")
check("crew section below threshold (no crew_size data)", crew["renderable"] is False)

print("=== 6. Insufficient overall data holds ===")
db5=FakeDB(); rs5=ReportService(db5)
for i in range(3):
    rs5.upsert_production_signal(report_id=f"x{i}", report_row={"id":f"sx{i}","created_at":"2026-05-15T00:00:00Z"},
        request_metadata={"budget_amount":1_000_000,"budget_currency":"GBP","b2b_consent":True,
                          "production_country":"UK","format":"feature","genre":["Drama"]})
b2b5=B2BService(db5); pkg5=PackageService(b2b5)
prev5=pkg5.preview(section_keys=["sig_territory_home"], period_start=date(2026,1,1), period_end=date(2026,12,31))
check("3 signals -> overall threshold NOT met", prev5["overall_threshold_met"] is False)
check("section flagged insufficient_overall", prev5["sections"][0]["status"]=="insufficient_overall")

print(f"\n==== {PASS} passed, {FAIL} failed ====")
sys.exit(1 if FAIL else 0)
