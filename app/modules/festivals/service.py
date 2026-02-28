from app.core.database_client import DatabaseClient
from app.modules.admin.service import _festival_from_db


class FestivalsService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def get_festivals(self) -> list[dict]:
        result = (
            self.supabase.table("film_festivals")
            .select("*")
            .execute()
        )
        rows = [_festival_from_db(row) for row in (result.data or [])]
        # Sort by days until next deadline ascending; festivals with no upcoming
        # deadline sort to the end.
        rows.sort(key=lambda r: (r.get("daysUntilNextDeadline") is None, r.get("daysUntilNextDeadline") or 0))
        return rows
