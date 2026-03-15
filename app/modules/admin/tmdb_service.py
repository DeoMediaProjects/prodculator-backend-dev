"""TMDB API integration for syncing comparable productions."""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx

from app.core.database_client import DatabaseClient
from app.core.territories import resolve_territory

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.themoviedb.org/3"


class TMDBService:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        params["api_key"] = self.api_key
        resp = httpx.get(f"{_BASE_URL}{path}", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def search_movie(self, query: str, year: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query": query}
        if year:
            params["year"] = year
        data = self._get("/search/movie", params)
        return data.get("results", [])

    def discover_movies(
        self,
        *,
        year: int | None = None,
        with_genres: str | None = None,
        page: int = 1,
        sort_by: str = "revenue.desc",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"page": page, "sort_by": sort_by}
        if year:
            params["primary_release_year"] = year
        if with_genres:
            params["with_genres"] = with_genres
        return self._get("/discover/movie", params)

    def get_movie_details(self, tmdb_id: int) -> dict[str, Any]:
        return self._get(f"/movie/{tmdb_id}")

    def sync_popular(
        self,
        db: DatabaseClient,
        *,
        pages: int = 3,
    ) -> dict[str, Any]:
        """Fetch top-revenue movies from TMDB and upsert into comparable_productions."""
        imported = 0
        skipped = 0
        total = 0

        for page in range(1, pages + 1):
            discovery = self.discover_movies(page=page, sort_by="revenue.desc")
            movies = discovery.get("results", [])
            if not movies:
                break

            for movie in movies:
                total += 1
                tmdb_id = str(movie["id"])

                try:
                    details = self.get_movie_details(movie["id"])
                except httpx.HTTPStatusError:
                    logger.warning("Failed to fetch TMDB details for id=%s", tmdb_id)
                    skipped += 1
                    continue

                budget = details.get("budget", 0)
                if not budget:
                    skipped += 1
                    continue

                genres = [g["name"] for g in details.get("genres", [])]
                countries = details.get("production_countries", [])
                raw_territory = countries[0]["name"] if countries else "Unknown"
                # Normalise TMDB country names (e.g. "United States of America")
                # to canonical Territory labels (e.g. "United States")
                resolved = resolve_territory(raw_territory)
                territory = resolved.label if resolved else raw_territory
                release_date = details.get("release_date", "")
                year = int(release_date[:4]) if release_date and len(release_date) >= 4 else None

                row_data: dict[str, Any] = {
                    "title": details.get("title", ""),
                    "year": year,
                    "budget_usd": budget,
                    "primary_territory": territory,
                    "genre": genres,
                    "tmdb_id": tmdb_id,
                    "source": "TMDB",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }

                existing = (
                    db.table("comparable_productions")
                    .select("id")
                    .eq("tmdb_id", tmdb_id)
                    .execute()
                    .data
                )

                if existing:
                    db.table("comparable_productions").update(row_data).eq("tmdb_id", tmdb_id).execute()
                else:
                    now = datetime.now(timezone.utc).isoformat()
                    row_data["id"] = str(uuid4())
                    row_data["created_at"] = now
                    db.table("comparable_productions").insert(row_data).execute()

                imported += 1

        logger.info("TMDB sync complete: imported=%d skipped=%d total=%d", imported, skipped, total)
        return {"imported": imported, "skipped": skipped, "total": total}
