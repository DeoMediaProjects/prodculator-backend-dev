from app.core.database_client import DatabaseClient


class FestivalsService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def get_festivals(self) -> list[dict]:
        result = (
            self.supabase.table("film_festivals")
            .select("*")
            .order("submission_deadline", desc=False)
            .execute()
        )
        return result.data or []

