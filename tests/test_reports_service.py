from datetime import date, timedelta

from sqlalchemy.exc import NoSuchTableError

from app.modules.reports.service import ReportService


class FakeResult:
    def __init__(self, data=None):
        self.data = data


class FakeQuery:
    def __init__(self, table_name: str, rows):
        self.table_name = table_name
        self.rows = rows

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def in_(self, *_args, **_kwargs):
        if self.table_name == "film_festivals":
            raise KeyError("status")
        return self

    def order(self, *_args, **_kwargs):
        return self

    def execute(self):
        return FakeResult(self.rows)


class FakeSupabase:
    def __init__(self):
        self.tables = {
            "incentive_programs": [{"id": "i1", "territory": "UK", "status": "active"}],
            "crew_costs": [{"id": "c1", "territory": "UK"}],
            "grant_opportunities": [{"id": "g1", "status": "open"}],
            "film_festivals": [{"id": "f1", "status": "open"}],
        }

    def table(self, table_name: str):
        if table_name == "comparable_productions":
            raise NoSuchTableError(table_name)
        return FakeQuery(table_name, self.tables.get(table_name, []))


def test_load_analysis_datasets_tolerates_missing_optional_table():
    service = ReportService(FakeSupabase())

    datasets = service._load_analysis_datasets()

    assert datasets["comparables"] == []
    assert len(datasets["incentives"]) == 1
    assert len(datasets["crew_costs"]) == 1
    assert len(datasets["grants"]) == 1
    assert len(datasets["festivals"]) == 1


def test_load_analysis_datasets_handles_festivals_without_status_column():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    class FestivalSupabase(FakeSupabase):
        def __init__(self):
            super().__init__()
            self.tables["film_festivals"] = [
                {
                    "id": "f-upcoming",
                    "name": "Open Festival",
                    "submission_deadline": tomorrow,
                },
                {
                    "id": "f-closed",
                    "name": "Closed Festival",
                    "submission_deadline": yesterday,
                },
            ]

    service = ReportService(FestivalSupabase())
    datasets = service._load_analysis_datasets()

    assert [f["id"] for f in datasets["festivals"]] == ["f-upcoming"]
