# Fix Production Migration Conflict

## Problem

The production database already has the `data_sources` table, but Alembic thinks migration `f1a2b3c4d5e6` hasn't been applied yet.

## Solution Options

### Option 1: Mark Migration as Applied (Recommended)

This tells Alembic that the migration has already been applied without actually running it.

```bash
# Connect to production database
# Update the alembic_version table to mark migration as complete
alembic stamp f1a2b3c4d5e6
```

Then continue with remaining migrations:

```bash
alembic upgrade head
```

### Option 2: Manually Update Migration History in Production

If Option 1 doesn't work, directly update the database:

```sql
-- Connect to production PostgreSQL
-- Check current version
SELECT * FROM alembic_version;

-- Update to the migration just after data_sources table was created
UPDATE alembic_version SET version_num = 'f1a2b3c4d5e6' WHERE version_num = 'a5dcb3d855ee';
```

Then run:

```bash
alembic upgrade head
```

### Option 3: Skip the Problematic Parts (Already Done)

We've already made the migration idempotent by:

1. Checking if table exists before creating it
2. Checking if data exists before inserting it
3. Adding created_at/updated_at timestamps to all inserts

However, Python's bytecode cache prevented the changes from being loaded.

## Steps to Execute

### For Production:

1. **Clear Python cache first:**

```bash
cd /path/to/prodculator_backend
rm -rf alembic/versions/__pycache__
rm -rf alembic/__pycache__
```

2. **Use stamp to mark current state:**

```bash
# Set environment to production
export DB_URL="your_production_db_url"

# Stamp the migration as applied
alembic stamp f1a2b3c4d5e6

# Verify
alembic current

# Continue with remaining migrations
alembic upgrade head
```

### Alternative: Direct SQL Update

If you have direct database access:

```sql
-- 1. Check what's in alembic_version
SELECT * FROM alembic_version;

-- 2. Update to mark f1a2b3c4d5e6 as applied
UPDATE alembic_version
SET version_num = 'f1a2b3c4d5e6'
WHERE version_num = (SELECT version_num FROM alembic_version LIMIT 1);

-- 3. Verify
SELECT * FROM alembic_version;
```

Then run remaining migrations:

```bash
alembic upgrade head
```

## Migration Chain

Current state: `d738ccebf985` → Need to apply:

- `fe6e41788a05` (add_user_blocked_fields)
- `a5dcb3d855ee` (add_tmdb_id_and_source_to_comparable_productions)
- `f1a2b3c4d5e6` (create_data_sources_table) ← **THIS ONE EXISTS**
- Any remaining migrations to head

## Recommendation

Use `alembic stamp f1a2b3c4d5e6` after the previous migrations succeed, or manually update the alembic_version table in production to skip this specific migration.
