"""Initialise the AURUM AI database (same as `python -m aurum.cli init-db`).

Usage (from the project root):  python scripts/setup_database.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aurum.config import get_settings  # noqa: E402
from aurum.database.connection import check_database, engine_from_settings, init_database  # noqa: E402


def main() -> int:
    settings = get_settings()
    engine = engine_from_settings(settings)
    try:
        steps = init_database(engine)
        info = check_database(engine)
    except Exception as exc:
        print(f"ERROR: could not initialise {settings.safe_database_url}: {type(exc).__name__}: {exc}")
        return 1
    print(f"Schema ready on {settings.safe_database_url}: {', '.join(steps)}")
    print(f"dialect={info['dialect']} timescaledb={info['timescaledb']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
