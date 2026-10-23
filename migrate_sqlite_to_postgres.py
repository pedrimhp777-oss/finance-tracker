"""Copy existing SQLite users and transactions into an empty PostgreSQL database.

Set DATABASE_URL to the PostgreSQL destination before running this script.
The SQLite source is read only; this script does not delete or modify it.
"""

import argparse
from datetime import timezone
from pathlib import Path

from sqlalchemy import create_engine, select, text

from app.database import engine as target_engine
from app.models import Base, Transaction, User


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "sqlite_file",
        nargs="?",
        default="finance.db",
        help="Path to the current SQLite file (default: ./finance.db)",
    )
    args = parser.parse_args()
    sqlite_file = Path(args.sqlite_file).expanduser().resolve()
    if not sqlite_file.is_file():
        raise SystemExit(f"SQLite database not found: {sqlite_file}")

    Base.metadata.create_all(bind=target_engine)
    source_engine = create_engine(f"sqlite:///{sqlite_file.as_posix()}")
    try:
        with source_engine.connect() as source:
            users = [dict(row._mapping) for row in source.execute(select(User.__table__))]
            transactions = [
                dict(row._mapping) for row in source.execute(select(Transaction.__table__))
            ]
        for row in users:
            created_at = row.get("created_at")
            if created_at is not None and created_at.tzinfo is None:
                row["created_at"] = created_at.replace(tzinfo=timezone.utc)
        for row in transactions:
            created_at = row.get("date")
            if created_at is not None and created_at.tzinfo is None:
                row["date"] = created_at.replace(tzinfo=timezone.utc)

        with target_engine.begin() as target:
            existing_users = target.execute(text("SELECT count(*) FROM users")).scalar_one()
            existing_transactions = target.execute(
                text("SELECT count(*) FROM transactions")
            ).scalar_one()
            if existing_users or existing_transactions:
                raise SystemExit(
                    "The PostgreSQL database is not empty. Migration stopped without copying data."
                )

            if users:
                target.execute(User.__table__.insert(), users)
            if transactions:
                target.execute(Transaction.__table__.insert(), transactions)

            for table_name in ("users", "transactions"):
                target.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), "
                        f"EXISTS(SELECT 1 FROM {table_name}))"
                    ),
                    {"table_name": table_name},
                )

        print(f"Copied {len(users)} users and {len(transactions)} transactions.")
        print("The SQLite source was left unchanged.")
    finally:
        source_engine.dispose()


if __name__ == "__main__":
    main()
