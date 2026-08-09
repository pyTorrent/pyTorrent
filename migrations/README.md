# Database migrations

Numbered `*.sql` files are loaded by `pytorrent/migrations.py` and recorded in the
`schema_migrations` table. `state.json` defines the newest known migration and the
oldest consecutive migration range that has been retired.

Each migration may include a one-line `-- pytorrent:applied-if SELECT ...;` probe.
It lets an installation created before `schema_migrations` recognize schema changes
that are already present without hardcoding migration-specific rules in Python.

## Adding a migration

1. Add the next numbered SQL file, for example `0009_example.sql`.
2. Add an `applied-if` probe when an older installation may already contain the change.
3. Update `latest_version` in `state.json`.
4. Keep an executed migration immutable; its SHA-256 checksum is stored in the database.

## Retiring old migrations

Old SQL files may be deleted after the supported upgrade window has passed. Raise
`retired_through` in `state.json` to the highest consecutively retired version, then
delete those SQL files. Databases that already contain matching `schema_migrations`
rows continue normally and do not need the deleted files.

A database that still needs a retired migration fails with an explicit upgrade-path
error instead of starting with a partially migrated schema. Such a database must first
upgrade through a pyTorrent version that still contains the required SQL files.

Fresh databases are created from the current schema snapshot and record historical
migrations as a baseline, so retired SQL files are not required for new installations.

## Current schema snapshot

The canonical schema for fresh databases lives in `../schema.sql`. SQL migrations are upgrade steps for existing databases only. A `CREATE TABLE IF NOT EXISTS` statement does not add missing columns to an existing SQLite table, so structural changes that current application code depends on still require a migration even when the same columns already exist in `schema.sql`.
