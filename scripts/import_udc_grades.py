from __future__ import annotations

import argparse
from pathlib import Path

from src.core.config import load_settings
from src.core.db import Database
from src.providers.grade_provider import GradeProvider


def main():
    parser = argparse.ArgumentParser(description="Import a UDC grade export into the bot database.")
    parser.add_argument("path", help="Path to a CSV or JSON file exported or captured from UDC")
    args = parser.parse_args()

    input_path = Path(args.path)
    if not input_path.exists():
        raise SystemExit(f"File not found: {input_path}")

    settings = load_settings()
    db = Database(settings.database_path)
    provider = GradeProvider(db=db)

    if input_path.suffix.lower() == ".csv":
        provider.csv_path = input_path
    else:
        provider.json_path = input_path

    import asyncio

    asyncio.run(provider.refresh())
    print(provider.last_refresh)
    print(provider.last_error)


if __name__ == "__main__":
    main()
