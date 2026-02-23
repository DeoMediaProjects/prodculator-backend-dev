from app.core.database_client import DatabaseClient


class GrantsService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def get_grants(self, territory: str | None = None) -> list[dict]:
        query = self.supabase.table("grant_opportunities").select("*")
        if territory:
            query = query.eq("territory", territory)
        result = query.order("deadline", desc=False).execute()
        return result.data or []

