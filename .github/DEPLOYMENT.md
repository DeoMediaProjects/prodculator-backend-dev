# How this service deploys

Railway, from GitHub, on push to `main`. There is no CI gate on that: Railway
deploys whether or not the test job passed.

## Migrations are not automatic

Nothing in the container runs Alembic — the image's `CMD` starts uvicorn and
nothing else. Migrations are run by hand, locally, against the public database
URL:

```bash
DB_URL="postgresql+psycopg2://…@…proxy.rlwy.net:PORT/railway" alembic upgrade head
```

Take a backup first. Railway's Postgres service has a **Backups** tab; the
`grant_opportunities` and `incentive_programs` tables are the ones the data
migrations rewrite.

Because deploys are automatic and migrations are not, **code and schema drift
apart by default**. On 20 September 2026 production was twelve migrations
behind while running code that assumed them. `fix_production_migration.md`
records an earlier instance of the same thing. Run `alembic upgrade head`
whenever a migration merges, not when something breaks.

## The workflow that used to live here

`deploy.yml` SSH'd to a VPS and ran `alembic upgrade head` there. That VPS is
not where this service runs, its `SSH_HOST` secret was never set, and the job
failed on every push with `Error: missing server host` — which made every run
red even when the tests passed, and trained everyone to ignore the red.

It was removed rather than fixed: a workflow pointing at infrastructure nobody
uses is worse than no workflow, because it looks like deployment is covered.

If automated migrations are wanted later, the place for them is a Railway
pre-deploy command, not an SSH action. Weigh it carefully — a failed migration
would then take the service down rather than leaving it stale.
