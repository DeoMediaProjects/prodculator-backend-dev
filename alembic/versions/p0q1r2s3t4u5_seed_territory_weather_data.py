"""seed_territory_weather_data

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-03-13 10:20:00.000000

Seeds the territory_weather table with monthly climate data for 15 priority
territories (~180 rows).  Data sourced from public climate databases
(World Bank Climate, national meteorological offices).
"""
from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = "p0q1r2s3t4u5"
down_revision = "o9p0q1r2s3t4"
branch_labels = None
depends_on = None

_NOW = datetime(2026, 3, 13, tzinfo=timezone.utc).isoformat()
_SOURCE = "World Bank Climate / National Met Offices"

# Compact format: (territory, month, high_c, low_c, rain_mm, daylight_h,
#                   storm_risk, notes, exterior_score)

_WEATHER_DATA = [
    # ── United Kingdom (London baseline) ─────────────────────────────────
    ("United Kingdom", 1, 8, 2, 55, 8.0, "medium", "Short days; overcast; occasional snow", 35),
    ("United Kingdom", 2, 8, 2, 40, 9.5, "medium", "Cold; improving daylight", 40),
    ("United Kingdom", 3, 11, 3, 37, 11.5, "low", "Transitional; unpredictable showers", 55),
    ("United Kingdom", 4, 14, 5, 37, 13.5, "low", "Mild; good production window starting", 70),
    ("United Kingdom", 5, 17, 8, 49, 15.5, "low", "Warm; long daylight; excellent conditions", 85),
    ("United Kingdom", 6, 20, 11, 45, 16.5, "low", "Peak summer; longest days", 90),
    ("United Kingdom", 7, 23, 13, 45, 16.0, "low", "Warmest month; occasional thunderstorms", 88),
    ("United Kingdom", 8, 22, 13, 50, 14.5, "low", "Warm; slightly increasing rain", 85),
    ("United Kingdom", 9, 19, 10, 49, 12.5, "low", "Mild; good autumn light", 75),
    ("United Kingdom", 10, 15, 8, 69, 10.5, "medium", "Cooling; rain increasing; shorter days", 55),
    ("United Kingdom", 11, 11, 4, 59, 9.0, "medium", "Cold; damp; limited daylight", 40),
    ("United Kingdom", 12, 8, 2, 55, 7.5, "medium", "Shortest days; cold; holiday period", 30),

    # ── Scotland ─────────────────────────────────────────────────────────
    ("Scotland", 1, 6, 1, 90, 7.0, "high", "Very short days; rain/snow; gale risk", 20),
    ("Scotland", 2, 7, 1, 65, 8.5, "high", "Cold; wet; improving daylight slowly", 25),
    ("Scotland", 3, 9, 2, 60, 11.0, "medium", "Cold; showers; variable conditions", 40),
    ("Scotland", 4, 12, 3, 50, 13.5, "medium", "Cool; rain decreasing; usable window", 60),
    ("Scotland", 5, 15, 6, 55, 16.0, "low", "Mild; long daylight; midges start", 78),
    ("Scotland", 6, 17, 9, 55, 17.5, "low", "Best conditions; longest days; midges peak", 85),
    ("Scotland", 7, 19, 11, 60, 17.0, "low", "Warmest; occasional heavy rain", 82),
    ("Scotland", 8, 18, 10, 70, 15.0, "low", "Warm; rain increasing; still good light", 78),
    ("Scotland", 9, 15, 8, 80, 12.5, "medium", "Cooling; autumn storms starting", 60),
    ("Scotland", 10, 12, 5, 95, 10.0, "high", "Wet; windy; rapidly shortening days", 35),
    ("Scotland", 11, 8, 3, 85, 8.0, "high", "Cold; wet; very short days", 22),
    ("Scotland", 12, 6, 1, 90, 7.0, "high", "Shortest days; storms; holiday period", 18),

    # ── South Africa (Gauteng / Johannesburg) ────────────────────────────
    ("South Africa", 1, 26, 15, 125, 13.5, "high", "Peak rainy season; afternoon thunderstorms daily", 40),
    ("South Africa", 2, 25, 15, 90, 13.0, "high", "Rainy season; frequent afternoon storms in Gauteng", 45),
    ("South Africa", 3, 24, 13, 80, 12.0, "medium", "Late rainy season; storms decreasing", 55),
    ("South Africa", 4, 21, 10, 50, 11.5, "low", "Dry season begins; excellent exterior conditions", 80),
    ("South Africa", 5, 19, 5, 15, 10.5, "low", "Dry; sunny; ideal production weather", 92),
    ("South Africa", 6, 16, 2, 8, 10.0, "low", "Winter dry season; clear skies; cold mornings", 90),
    ("South Africa", 7, 17, 3, 5, 10.5, "low", "Driest month; perfect exterior conditions", 95),
    ("South Africa", 8, 20, 5, 5, 11.0, "low", "Dry; warming; excellent conditions", 93),
    ("South Africa", 9, 23, 9, 20, 12.0, "low", "Warm; dry; spring arriving", 88),
    ("South Africa", 10, 24, 12, 70, 12.5, "medium", "Spring storms beginning; variable", 65),
    ("South Africa", 11, 25, 14, 105, 13.0, "high", "Rainy season starts; thunderstorms", 48),
    ("South Africa", 12, 26, 15, 110, 13.5, "high", "Full rainy season; daily storms; holiday period", 42),

    # ── Malta ────────────────────────────────────────────────────────────
    ("Malta", 1, 15, 9, 89, 10.0, "medium", "Mild winter; occasional storms", 55),
    ("Malta", 2, 15, 9, 52, 10.5, "medium", "Cool; improving; some rain", 60),
    ("Malta", 3, 17, 10, 41, 12.0, "low", "Spring starting; good conditions", 72),
    ("Malta", 4, 20, 12, 14, 13.0, "low", "Warm; very little rain; excellent", 88),
    ("Malta", 5, 24, 15, 7, 14.5, "low", "Warm/hot; dry; excellent production weather", 93),
    ("Malta", 6, 28, 19, 3, 15.0, "low", "Hot; completely dry; very long days", 90),
    ("Malta", 7, 31, 22, 0, 14.5, "low", "Peak heat; completely dry; heat risk for crew", 82),
    ("Malta", 8, 31, 22, 7, 13.5, "low", "Very hot; humid; crew heat management needed", 78),
    ("Malta", 9, 28, 20, 40, 12.5, "low", "Warm; occasional rain returning", 82),
    ("Malta", 10, 24, 17, 90, 11.5, "medium", "Warm; rain increasing; still usable", 68),
    ("Malta", 11, 20, 13, 80, 10.0, "medium", "Cool; wetter; some storms", 55),
    ("Malta", 12, 16, 10, 100, 9.5, "medium", "Cool; wettest month; limited daylight", 48),

    # ── Hungary (Budapest) ───────────────────────────────────────────────
    ("Hungary", 1, 1, -4, 40, 9.0, "medium", "Cold; short days; snow possible", 30),
    ("Hungary", 2, 4, -3, 35, 10.0, "medium", "Cold; improving daylight", 35),
    ("Hungary", 3, 10, 1, 30, 12.0, "low", "Transitional; cool; rain possible", 55),
    ("Hungary", 4, 17, 6, 40, 13.5, "low", "Pleasant; good production window", 75),
    ("Hungary", 5, 22, 11, 60, 15.0, "low", "Warm; longer days; occasional thunderstorms", 82),
    ("Hungary", 6, 25, 14, 65, 16.0, "low", "Warm/hot; some storms; good conditions", 80),
    ("Hungary", 7, 28, 16, 50, 15.5, "low", "Hot; occasional storms; peak summer", 78),
    ("Hungary", 8, 27, 15, 55, 14.0, "low", "Hot; some rain; good conditions", 80),
    ("Hungary", 9, 22, 11, 40, 12.5, "low", "Warm; excellent autumn light", 82),
    ("Hungary", 10, 15, 5, 40, 11.0, "low", "Cool; golden light; shorter days", 65),
    ("Hungary", 11, 7, 1, 50, 9.5, "medium", "Cold; overcast; rain", 38),
    ("Hungary", 12, 3, -2, 45, 8.5, "medium", "Cold; short days; holiday period", 28),

    # ── Ireland ──────────────────────────────────────────────────────────
    ("Ireland", 1, 8, 2, 80, 8.0, "high", "Wet; windy; short days; storm risk", 28),
    ("Ireland", 2, 8, 2, 55, 9.5, "medium", "Cold; wet; improving daylight", 35),
    ("Ireland", 3, 10, 3, 55, 11.5, "medium", "Cool; showers; variable", 48),
    ("Ireland", 4, 12, 4, 50, 14.0, "medium", "Mild; showers; usable window starting", 62),
    ("Ireland", 5, 15, 7, 55, 16.0, "low", "Mild; long days; best production month", 78),
    ("Ireland", 6, 18, 10, 55, 17.0, "low", "Warm; longest days; good conditions", 82),
    ("Ireland", 7, 19, 11, 55, 16.5, "low", "Warmest; occasional heavy rain", 78),
    ("Ireland", 8, 19, 11, 70, 15.0, "low", "Warm; rain increasing", 72),
    ("Ireland", 9, 17, 9, 70, 12.5, "medium", "Cooling; wetter; autumn storms", 58),
    ("Ireland", 10, 13, 7, 80, 10.5, "high", "Wet; windy; rapidly shortening days", 38),
    ("Ireland", 11, 10, 4, 75, 8.5, "high", "Cold; wet; storms; short days", 28),
    ("Ireland", 12, 8, 3, 80, 7.5, "high", "Wettest; storms; shortest days; holidays", 22),

    # ── Spain (Madrid baseline) ──────────────────────────────────────────
    ("Spain", 1, 10, 2, 33, 9.5, "low", "Cool; dry; clear skies", 60),
    ("Spain", 2, 12, 3, 35, 10.5, "low", "Cool; occasional rain", 65),
    ("Spain", 3, 16, 5, 25, 12.0, "low", "Warming; excellent conditions starting", 78),
    ("Spain", 4, 18, 7, 45, 13.5, "low", "Pleasant; occasional spring showers", 80),
    ("Spain", 5, 22, 11, 40, 14.5, "low", "Warm; good conditions", 88),
    ("Spain", 6, 28, 16, 20, 15.0, "low", "Hot; dry; very long days", 85),
    ("Spain", 7, 33, 19, 10, 14.5, "low", "Peak heat; extreme temperatures possible", 72),
    ("Spain", 8, 32, 19, 10, 13.5, "low", "Very hot; crew heat management needed", 70),
    ("Spain", 9, 27, 15, 25, 12.5, "low", "Warm; excellent autumn light", 85),
    ("Spain", 10, 20, 10, 50, 11.0, "low", "Pleasant; occasional rain", 75),
    ("Spain", 11, 14, 5, 55, 10.0, "medium", "Cool; wetter; still mild", 62),
    ("Spain", 12, 10, 2, 40, 9.5, "low", "Cool; dry; short days; holidays", 55),

    # ── France (Paris baseline) ──────────────────────────────────────────
    ("France", 1, 7, 2, 50, 8.5, "medium", "Cold; overcast; short days", 35),
    ("France", 2, 8, 2, 40, 10.0, "low", "Cold; improving daylight", 42),
    ("France", 3, 12, 4, 45, 12.0, "low", "Cool; transitional; variable", 58),
    ("France", 4, 16, 7, 45, 13.5, "low", "Pleasant; good conditions starting", 72),
    ("France", 5, 20, 10, 60, 15.5, "low", "Warm; occasional showers; good light", 82),
    ("France", 6, 23, 13, 50, 16.0, "low", "Warm; long days; excellent conditions", 88),
    ("France", 7, 25, 15, 55, 15.5, "low", "Warmest; occasional thunderstorms", 85),
    ("France", 8, 25, 15, 45, 14.0, "low", "Warm; good conditions", 85),
    ("France", 9, 21, 12, 50, 12.5, "low", "Mild; beautiful autumn light", 80),
    ("France", 10, 16, 8, 60, 11.0, "low", "Cool; rain increasing; shorter days", 60),
    ("France", 11, 10, 5, 50, 9.0, "medium", "Cool; overcast; damp", 40),
    ("France", 12, 7, 2, 55, 8.0, "medium", "Cold; short days; holiday period", 32),

    # ── Germany (Berlin baseline) ────────────────────────────────────────
    ("Germany", 1, 3, -2, 40, 8.5, "medium", "Cold; short days; snow possible", 28),
    ("Germany", 2, 4, -2, 33, 10.0, "medium", "Cold; improving daylight", 32),
    ("Germany", 3, 9, 1, 38, 12.0, "low", "Transitional; cool; rain", 50),
    ("Germany", 4, 15, 4, 35, 14.0, "low", "Pleasant; good conditions", 72),
    ("Germany", 5, 20, 9, 55, 15.5, "low", "Warm; excellent production weather", 82),
    ("Germany", 6, 23, 12, 60, 16.5, "low", "Warm; longest days; some storms", 85),
    ("Germany", 7, 25, 14, 55, 16.0, "low", "Warmest; thunderstorms possible", 82),
    ("Germany", 8, 25, 14, 55, 14.5, "low", "Warm; good conditions", 82),
    ("Germany", 9, 20, 10, 40, 12.5, "low", "Mild; excellent light", 78),
    ("Germany", 10, 13, 5, 35, 10.5, "low", "Cool; dry autumn; shorter days", 58),
    ("Germany", 11, 7, 2, 40, 9.0, "medium", "Cold; overcast; rain", 35),
    ("Germany", 12, 4, -1, 45, 8.0, "medium", "Cold; short days; holiday period", 25),

    # ── Czech Republic (Prague) ──────────────────────────────────────────
    ("Czech Republic", 1, 1, -4, 23, 8.5, "medium", "Cold; short days; snow", 28),
    ("Czech Republic", 2, 3, -3, 22, 10.0, "low", "Cold; dry; improving daylight", 35),
    ("Czech Republic", 3, 8, 0, 28, 12.0, "low", "Cool; spring starting", 52),
    ("Czech Republic", 4, 14, 4, 32, 14.0, "low", "Pleasant; good production window", 72),
    ("Czech Republic", 5, 19, 9, 60, 15.5, "low", "Warm; occasional rain", 80),
    ("Czech Republic", 6, 22, 12, 65, 16.5, "low", "Warm; some storms; long days", 82),
    ("Czech Republic", 7, 24, 14, 65, 16.0, "low", "Warmest; thunderstorms possible", 78),
    ("Czech Republic", 8, 24, 13, 60, 14.5, "low", "Warm; good conditions", 80),
    ("Czech Republic", 9, 19, 9, 35, 12.5, "low", "Mild; excellent light; dry", 82),
    ("Czech Republic", 10, 13, 5, 30, 10.5, "low", "Cool; golden light", 62),
    ("Czech Republic", 11, 6, 1, 30, 9.0, "medium", "Cold; overcast", 35),
    ("Czech Republic", 12, 2, -2, 25, 8.0, "medium", "Cold; short days; holidays", 25),

    # ── Australia (New South Wales / Sydney) ─────────────────────────────
    ("Australia", 1, 26, 19, 100, 14.0, "medium", "Hot; humid; summer storms", 72),
    ("Australia", 2, 26, 19, 115, 13.5, "medium", "Hottest; wettest; storm risk", 65),
    ("Australia", 3, 25, 17, 130, 12.0, "high", "Warm; wettest month; La Niña flood risk", 55),
    ("Australia", 4, 22, 14, 125, 11.0, "medium", "Autumn; cooling; frequent rain", 60),
    ("Australia", 5, 19, 11, 120, 10.5, "medium", "Cool; rain; variable", 58),
    ("Australia", 6, 17, 9, 130, 10.0, "medium", "Winter; cool; wet", 55),
    ("Australia", 7, 16, 7, 95, 10.0, "low", "Coolest; drier; clear skies", 65),
    ("Australia", 8, 18, 8, 80, 11.0, "low", "Cool; drier; improving", 72),
    ("Australia", 9, 20, 11, 60, 12.0, "low", "Spring starting; pleasant", 78),
    ("Australia", 10, 22, 13, 75, 13.0, "low", "Warm; good conditions; dry", 80),
    ("Australia", 11, 24, 16, 85, 14.0, "low", "Warm; excellent conditions", 78),
    ("Australia", 12, 25, 17, 80, 14.5, "medium", "Hot; summer storms starting; holidays", 72),

    # ── Canada (Vancouver / BC) ──────────────────────────────────────────
    ("Canada", 1, 6, 1, 170, 8.5, "high", "Very wet; dark; rain dominant", 20),
    ("Canada", 2, 8, 2, 115, 10.0, "high", "Wet; improving daylight slowly", 28),
    ("Canada", 3, 10, 3, 100, 12.0, "medium", "Rain decreasing; cool; variable", 42),
    ("Canada", 4, 13, 5, 65, 14.0, "medium", "Cool; rain decreasing; usable", 62),
    ("Canada", 5, 16, 8, 55, 15.5, "low", "Mild; drier; good production weather", 78),
    ("Canada", 6, 19, 11, 45, 16.5, "low", "Warm; dry; excellent conditions", 88),
    ("Canada", 7, 22, 13, 30, 16.0, "low", "Warmest; driest; peak production season", 95),
    ("Canada", 8, 22, 13, 35, 14.5, "low", "Warm; dry; excellent conditions", 92),
    ("Canada", 9, 19, 10, 55, 12.5, "low", "Mild; rain returning; good light", 78),
    ("Canada", 10, 13, 7, 120, 10.5, "medium", "Cool; wet; autumn storms", 45),
    ("Canada", 11, 9, 3, 175, 8.5, "high", "Very wet; dark; storms", 22),
    ("Canada", 12, 6, 1, 180, 8.0, "high", "Wettest; darkest; holidays", 18),

    # ── USA — Georgia (Atlanta) ──────────────────────────────────────────
    ("Georgia (USA)", 1, 10, 0, 110, 10.0, "medium", "Cool; occasional ice; variable", 55),
    ("Georgia (USA)", 2, 13, 2, 110, 11.0, "medium", "Cool; rainy; improving", 58),
    ("Georgia (USA)", 3, 18, 6, 110, 12.0, "medium", "Warming; spring storms", 68),
    ("Georgia (USA)", 4, 22, 10, 85, 13.0, "low", "Warm; pleasant; good conditions", 82),
    ("Georgia (USA)", 5, 27, 15, 85, 14.0, "low", "Warm/hot; some storms; good", 78),
    ("Georgia (USA)", 6, 31, 20, 100, 14.5, "medium", "Hot; humid; afternoon storms", 70),
    ("Georgia (USA)", 7, 32, 21, 115, 14.0, "medium", "Hottest; very humid; storms", 62),
    ("Georgia (USA)", 8, 32, 21, 95, 13.5, "medium", "Hot; humid; storms; hurricane season", 60),
    ("Georgia (USA)", 9, 28, 17, 85, 12.5, "medium", "Warm; hurricane season; variable", 68),
    ("Georgia (USA)", 10, 23, 11, 70, 11.5, "low", "Pleasant; excellent conditions", 85),
    ("Georgia (USA)", 11, 17, 5, 80, 10.5, "low", "Cool; mild; good conditions", 75),
    ("Georgia (USA)", 12, 11, 1, 100, 10.0, "medium", "Cool; rain; holidays", 55),

    # ── USA — New Mexico (Albuquerque) ───────────────────────────────────
    ("New Mexico", 1, 8, -6, 10, 10.0, "low", "Cold; dry; clear; excellent light", 55),
    ("New Mexico", 2, 12, -3, 10, 11.0, "low", "Cool; dry; improving", 62),
    ("New Mexico", 3, 17, 1, 10, 12.0, "low", "Warming; dry; excellent production weather", 78),
    ("New Mexico", 4, 22, 5, 10, 13.5, "low", "Warm; dry; excellent conditions", 88),
    ("New Mexico", 5, 27, 10, 12, 14.0, "low", "Warm; dry; excellent light quality", 92),
    ("New Mexico", 6, 33, 16, 15, 14.5, "low", "Hot; dry; extreme heat risk", 78),
    ("New Mexico", 7, 34, 18, 40, 14.0, "medium", "Monsoon season; afternoon thunderstorms", 68),
    ("New Mexico", 8, 32, 17, 45, 13.5, "medium", "Monsoon; afternoon storms; hot", 65),
    ("New Mexico", 9, 28, 13, 25, 12.5, "low", "Monsoon ending; warm; excellent light", 82),
    ("New Mexico", 10, 22, 5, 15, 11.5, "low", "Warm days; cool nights; excellent", 88),
    ("New Mexico", 11, 14, -1, 10, 10.5, "low", "Cool; dry; clear; good conditions", 72),
    ("New Mexico", 12, 8, -5, 12, 10.0, "low", "Cold; dry; clear; holidays", 55),

    # ── Nigeria (Lagos) ──────────────────────────────────────────────────
    ("Nigeria", 1, 32, 23, 15, 12.0, "low", "Hot; dry; harmattan haze possible", 65),
    ("Nigeria", 2, 33, 25, 30, 12.0, "low", "Hottest; dry; some haze", 62),
    ("Nigeria", 3, 33, 25, 55, 12.0, "medium", "Hot; humid; rain starting", 55),
    ("Nigeria", 4, 32, 24, 100, 12.5, "medium", "Rainy season starting; hot; humid", 45),
    ("Nigeria", 5, 31, 23, 175, 12.5, "high", "Full rainy season; heavy rain; flooding risk", 30),
    ("Nigeria", 6, 29, 23, 250, 12.5, "high", "Peak rain; very wet; flood risk; shooting difficult", 20),
    ("Nigeria", 7, 28, 22, 200, 12.5, "high", "Wet; cooler; persistent rain", 22),
    ("Nigeria", 8, 28, 22, 150, 12.5, "high", "Wet; rain easing slightly", 28),
    ("Nigeria", 9, 29, 22, 200, 12.5, "high", "Second rainy peak; very wet", 25),
    ("Nigeria", 10, 30, 23, 130, 12.0, "medium", "Rain decreasing; warm; humid", 42),
    ("Nigeria", 11, 32, 23, 35, 12.0, "low", "Dry season starting; hot", 62),
    ("Nigeria", 12, 32, 23, 15, 12.0, "low", "Dry; harmattan haze; holidays", 60),
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "territory_weather" not in inspector.get_table_names():
        return

    for row in _WEATHER_DATA:
        territory, month, high_c, low_c, rain_mm, daylight_h, storm, notes, score = row

        # Upsert: check if row already exists
        result = conn.execute(
            sa.text(
                "SELECT id FROM territory_weather "
                "WHERE territory = :territory AND month = :month "
                "LIMIT 1"
            ),
            {"territory": territory, "month": month},
        )
        existing = result.fetchone()

        params = {
            "territory": territory,
            "month": month,
            "avg_temp_high_c": high_c,
            "avg_temp_low_c": low_c,
            "avg_rainfall_mm": rain_mm,
            "avg_daylight_hours": daylight_h,
            "storm_risk": storm,
            "weather_notes": notes,
            "exterior_shoot_score": score,
            "source": _SOURCE,
            "last_verified_at": _NOW,
        }

        if existing:
            params["id"] = existing[0]
            conn.execute(
                sa.text(
                    "UPDATE territory_weather SET "
                    "avg_temp_high_c = :avg_temp_high_c, "
                    "avg_temp_low_c = :avg_temp_low_c, "
                    "avg_rainfall_mm = :avg_rainfall_mm, "
                    "avg_daylight_hours = :avg_daylight_hours, "
                    "storm_risk = :storm_risk, "
                    "weather_notes = :weather_notes, "
                    "exterior_shoot_score = :exterior_shoot_score, "
                    "source = :source, "
                    "last_verified_at = :last_verified_at "
                    "WHERE id = :id"
                ),
                params,
            )
        else:
            params["id"] = str(uuid4())
            conn.execute(
                sa.text(
                    "INSERT INTO territory_weather "
                    "(id, territory, month, avg_temp_high_c, avg_temp_low_c, "
                    "avg_rainfall_mm, avg_daylight_hours, storm_risk, weather_notes, "
                    "exterior_shoot_score, source, last_verified_at) "
                    "VALUES (:id, :territory, :month, :avg_temp_high_c, :avg_temp_low_c, "
                    ":avg_rainfall_mm, :avg_daylight_hours, :storm_risk, :weather_notes, "
                    ":exterior_shoot_score, :source, :last_verified_at)"
                ),
                params,
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "territory_weather" not in inspector.get_table_names():
        return

    # Remove all seeded rows
    territories = list({row[0] for row in _WEATHER_DATA})
    for territory in territories:
        conn.execute(
            sa.text("DELETE FROM territory_weather WHERE territory = :territory"),
            {"territory": territory},
        )
