from app.core.database_client import DatabaseClient


class WatchlistService:
    def __init__(self, supabase: DatabaseClient):
        self.supabase = supabase

    def get_watchlist(self, user_id: str) -> list[str]:
        result = (
            self.supabase.table("territory_watchlist")
            .select("territory")
            .eq("user_id", user_id)
            .execute()
        )
        return [row["territory"] for row in (result.data or []) if row.get("territory")]

    def add_territory(self, user_id: str, territory: str) -> None:
        # Upsert avoids duplicate rows when (user_id, territory) unique index exists.
        self.supabase.table("territory_watchlist").upsert(
            {"user_id": user_id, "territory": territory},
            on_conflict="user_id,territory",
        ).execute()

    def remove_territory(self, user_id: str, territory: str) -> None:
        (
            self.supabase.table("territory_watchlist")
            .delete()
            .eq("user_id", user_id)
            .eq("territory", territory)
            .execute()
        )

