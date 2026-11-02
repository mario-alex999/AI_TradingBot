# Schema migrations

`sql/schema.sql` is the idempotent v1 schema (`0001_initial`). Future changes go here as numbered,
forward-only files, e.g. `0002_add_market_depth.sql`.

* `python -m aurum.cli init-db` (or `python scripts/setup_database.py`) runs `schema.sql`, then every
  `*.sql` file in this folder whose name (without `.sql`) is not yet in `schema_migrations`, in name order.
* Each file runs once; its name is then recorded in `schema_migrations`.
* Write migrations with `IF NOT EXISTS` / `IF EXISTS` guards and never edit a migration that has been applied.
* Also update `aurum/database/models.py` so SQLite/dev databases match.
