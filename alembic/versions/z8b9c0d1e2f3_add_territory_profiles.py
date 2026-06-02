"""add territory_profiles table with seed data

Revision ID: z8b9c0d1e2f3
Revises: z7a8b9c0d1e2
Create Date: 2026-06-01 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'z8b9c0d1e2f3'
down_revision = 'z7a8b9c0d1e2'
branch_labels = None
depends_on = None

SEED_SQL = """
INSERT INTO territory_profiles (
    id, territory, iso_code,
    crew_depth_tier, crew_depth_score, crew_depth_notes,
    infrastructure_tier, infrastructure_score, infrastructure_notes,
    hemisphere
) VALUES
(gen_random_uuid(), 'United Kingdom', 'GB', 'established', 80, 'Pinewood, Leavesden, Shepperton. BECTU crew. Full post pipeline.', 'established', 80, 'Pinewood, Leavesden, Shepperton. Full post pipeline.', 'northern'),
(gen_random_uuid(), 'Ireland', 'IE', 'established', 80, 'Strong crew. Troy Studios. Limited stage vs UK.', 'growing', 60, 'Troy Studios. Limited stage vs UK.', 'northern'),
(gen_random_uuid(), 'France', 'FR', 'established', 80, 'Cité du Cinéma. CNC-supported. Deep Paris crew.', 'established', 80, 'Cité du Cinéma. CNC-supported.', 'northern'),
(gen_random_uuid(), 'Germany', 'DE', 'established', 80, 'Babelsberg, Bavaria Studios. VDT union.', 'established', 80, 'Babelsberg, Bavaria Studios.', 'northern'),
(gen_random_uuid(), 'Australia', 'AU', 'established', 80, 'Village Roadshow, Fox Studios, Docklands. MEAA.', 'established', 80, 'Village Roadshow, Fox Studios, Docklands.', 'southern'),
(gen_random_uuid(), 'Canada', 'CA', 'established', 80, 'IATSE/Teamsters. Major studio presence BC/Ontario/Quebec.', 'established', 80, 'Major studio presence BC/Ontario/Quebec.', 'northern'),
(gen_random_uuid(), 'Czech Republic', 'CZ', 'established', 85, 'Barrandov + Brno. Override 85 — exceptional depth.', 'established', 85, 'Barrandov + Brno. Override 85.', 'northern'),
(gen_random_uuid(), 'Hungary', 'HU', 'established', 80, 'Origo, Korda. Strong art dept.', 'established', 75, 'Origo, Korda. Post pipeline smaller than CZ.', 'northern'),
(gen_random_uuid(), 'New Zealand', 'NZ', 'established', 75, 'WetaFX world-class. Stage lighter than AU.', 'growing', 65, 'WetaFX world-class. Stage lighter than AU.', 'southern'),
(gen_random_uuid(), 'South Africa', 'ZA', 'growing', 60, 'Cape Town + Joburg. Post mostly imported.', 'growing', 55, 'Cape Town + Joburg. Post mostly imported.', 'southern'),
(gen_random_uuid(), 'Spain', 'ES', 'growing', 60, 'Madrid/Barcelona growing. No major purpose-built studio.', 'growing', 55, 'No major purpose-built studio.', 'northern'),
(gen_random_uuid(), 'Italy', 'IT', 'growing', 55, 'Cinecittà significant. Outside Rome infrastructure thin.', 'growing', 55, 'Cinecittà significant. Outside Rome thin.', 'northern'),
(gen_random_uuid(), 'Belgium', 'BE', 'growing', 55, 'Brussels crew; limited stage.', 'growing', 50, 'Brussels crew; limited stage.', 'northern'),
(gen_random_uuid(), 'Netherlands', 'NL', 'growing', 55, 'Strong commercial crew. No major film studio.', 'growing', 55, 'No major film studio.', 'northern'),
(gen_random_uuid(), 'Portugal', 'PT', 'growing', 55, 'Lisbon crew developing. Very limited stage.', 'emerging', 40, 'Very limited stage.', 'northern'),
(gen_random_uuid(), 'Romania', 'RO', 'growing', 55, 'Bucharest capable. Media Pro Studios.', 'growing', 50, 'Media Pro Studios.', 'northern'),
(gen_random_uuid(), 'Serbia', 'RS', 'growing', 60, 'Belgrade growing fast. Override 60 — strong recent growth.', 'growing', 55, 'Belgrade growing fast.', 'northern'),
(gen_random_uuid(), 'Iceland', 'IS', 'growing', 50, 'Small but skilled crew. No permanent stage.', 'emerging', 35, 'No permanent stage.', 'northern'),
(gen_random_uuid(), 'Malta', 'MT', 'growing', 50, 'Mediterranean Studios. Small but experienced.', 'growing', 55, 'Mediterranean Studios.', 'northern'),
(gen_random_uuid(), 'Morocco', 'MA', 'growing', 50, 'Ouarzazate/Casablanca capable. Limited stage.', 'emerging', 35, 'Limited stage.', 'northern'),
(gen_random_uuid(), 'India', 'IN', 'established', 75, 'Mumbai deep crew. Ramoji Film City.', 'growing', 65, 'Ramoji Film City.', 'northern'),
(gen_random_uuid(), 'Japan', 'JP', 'growing', 55, 'Strong domestic; intl crew thinner. TOHO studios.', 'growing', 60, 'TOHO studios.', 'northern'),
(gen_random_uuid(), 'South Korea', 'KR', 'growing', 60, 'Post-Parasite boom. Strong VFX. Seoul growing.', 'growing', 60, 'Post-Parasite boom. Strong VFX.', 'northern'),
(gen_random_uuid(), 'Singapore', 'SG', 'growing', 50, 'Regional hub. Good post. Thin for large productions.', 'growing', 55, 'Regional hub. Good post.', 'northern'),
(gen_random_uuid(), 'Nigeria', 'NG', 'emerging', 35, 'Nollywood domestic-focused. Intl crew very thin.', 'emerging', 30, 'Nollywood domestic-focused.', 'northern'),
(gen_random_uuid(), 'New South Wales', 'AU-NSW', 'established', 80, 'Fox Studios Sydney. Primary AU hub.', 'established', 80, 'Fox Studios Sydney.', 'southern'),
(gen_random_uuid(), 'Victoria', 'AU-VIC', 'established', 75, 'Docklands. Strong Melbourne crew.', 'established', 75, 'Docklands.', 'southern'),
(gen_random_uuid(), 'Queensland', 'AU-QLD', 'established', 75, 'Village Roadshow Gold Coast.', 'established', 75, 'Village Roadshow Gold Coast.', 'southern'),
(gen_random_uuid(), 'British Columbia', 'CA-BC', 'established', 85, 'Vancouver = Hollywood North. IATSE 891. Override 85.', 'established', 85, 'Vancouver. Override 85.', 'northern'),
(gen_random_uuid(), 'Ontario', 'CA-ON', 'established', 80, 'Toronto. Pinewood Toronto. IATSE/Teamsters.', 'established', 80, 'Pinewood Toronto.', 'northern'),
(gen_random_uuid(), 'Quebec', 'CA-QC', 'established', 75, 'Montreal French crew deep. AQTIS.', 'growing', 65, 'Montreal. Anglophone HOD thinner.', 'northern'),
(gen_random_uuid(), 'Alberta', 'CA-AB', 'growing', 55, 'Calgary/Edmonton growing. Primarily landscape shoots.', 'growing', 50, 'Primarily landscape shoots.', 'northern'),
(gen_random_uuid(), 'Georgia (USA)', 'US-GA', 'established', 80, 'Trilith Studios. IATSE 479. High-volume US state.', 'established', 85, 'Trilith Studios.', 'northern'),
(gen_random_uuid(), 'New York', 'US-NY', 'established', 85, 'Steiner, Silvercup, Chelsea Piers. IATSE 52. Override 85.', 'established', 85, 'Steiner, Silvercup. Override 85.', 'northern'),
(gen_random_uuid(), 'California', 'US-CA', 'established', 85, 'Global benchmark. World''s deepest film crew base. Override 85.', 'established', 85, 'Global benchmark. Override 85.', 'northern'),
(gen_random_uuid(), 'Louisiana', 'US-LA', 'growing', 60, 'New Orleans strong. Celtic Media Centre. IATSE 478.', 'growing', 55, 'Celtic Media Centre.', 'northern'),
(gen_random_uuid(), 'New Mexico', 'US-NM', 'growing', 55, 'Albuquerque Studios. Growing post-Breaking Bad era.', 'growing', 50, 'Albuquerque Studios.', 'northern'),
(gen_random_uuid(), 'Illinois', 'US-IL', 'growing', 55, 'Chicago capable. Cinespace. Strong TV; thinner for features.', 'growing', 55, 'Cinespace.', 'northern'),
(gen_random_uuid(), 'Western Cape', 'ZA-WC', 'growing', 60, 'Cape Town strongest SA sub-territory.', 'growing', 55, 'Cape Town.', 'southern'),
(gen_random_uuid(), 'Gauteng', 'ZA-GT', 'growing', 55, 'Johannesburg. Primarily commercials/TV.', 'growing', 50, 'Johannesburg.', 'southern'),
(gen_random_uuid(), 'KwaZulu-Natal', 'ZA-KZN', 'emerging', 30, 'Durban early-stage. Location only.', 'emerging', 30, 'Durban early-stage.', 'southern'),
(gen_random_uuid(), 'Bavaria', 'DE-BY', 'established', 75, 'Bavaria Film Studios Munich.', 'established', 75, 'Bavaria Film Studios Munich.', 'northern'),
(gen_random_uuid(), 'Berlin', 'DE-BE', 'established', 80, 'Babelsberg. Deep indie/intl crew.', 'established', 80, 'Babelsberg.', 'northern'),
(gen_random_uuid(), 'Scotland', 'GB-SCT', 'growing', 60, 'Edinburgh/Glasgow capable. No major stage.', 'growing', 55, 'No major stage.', 'northern'),
(gen_random_uuid(), 'Wales', 'GB-WLS', 'growing', 55, 'Wolf Studios Wales. Strong BBC/TV heritage.', 'growing', 55, 'Wolf Studios Wales.', 'northern'),
(gen_random_uuid(), 'Northern Ireland', 'GB-NIR', 'growing', 55, 'Belfast Harbour Studios (GoT legacy). Above-size crew.', 'growing', 60, 'Belfast Harbour Studios.', 'northern'),
(gen_random_uuid(), 'Canary Islands', 'ES-CN', 'emerging', 35, 'Location-only. Crew imported from mainland Spain.', 'emerging', 30, 'Location-only.', 'northern'),
(gen_random_uuid(), 'Île-de-France', 'FR-IDF', 'established', 80, 'Paris region. Cité du Cinéma. Deepest French crew.', 'established', 80, 'Cité du Cinéma.', 'northern')
ON CONFLICT (territory) DO NOTHING;
"""


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS territory_profiles (
        id                      TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
        territory               TEXT NOT NULL UNIQUE,
        iso_code                TEXT,
        crew_depth_tier         TEXT NOT NULL DEFAULT 'emerging'
            CHECK (crew_depth_tier IN ('established','growing','emerging')),
        crew_depth_score        INTEGER NOT NULL DEFAULT 30
            CHECK (crew_depth_score BETWEEN 0 AND 100),
        crew_depth_notes        TEXT,
        infrastructure_tier     TEXT NOT NULL DEFAULT 'emerging'
            CHECK (infrastructure_tier IN ('established','growing','emerging')),
        infrastructure_score    INTEGER NOT NULL DEFAULT 30
            CHECK (infrastructure_score BETWEEN 0 AND 100),
        infrastructure_notes    TEXT,
        hemisphere              TEXT NOT NULL DEFAULT 'northern'
            CHECK (hemisphere IN ('northern','southern')),
        intl_productions_3yr    INTEGER,
        intl_productions_source TEXT,
        last_reviewed_at        TIMESTAMPTZ,
        reviewed_by             TEXT,
        review_notes            TEXT,
        created_at              TIMESTAMPTZ DEFAULT now(),
        updated_at              TIMESTAMPTZ DEFAULT now()
    );
    """)
    op.execute(SEED_SQL)


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS territory_profiles CASCADE')
