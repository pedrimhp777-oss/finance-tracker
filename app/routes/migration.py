"""Temporary, secret-protected SQLite import endpoint. Remove after migration."""

import os
import secrets
import tempfile
from datetime import timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile, status
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database import engine, get_db
from app.models import Transaction, User

router = APIRouter(prefix="/admin", tags=["Temporary migration"])
MAX_SQLITE_BYTES = 50 * 1024 * 1024


@router.post("/migrate-sqlite")
async def import_sqlite_database(
    sqlite_file: UploadFile = File(...),
    x_migration_secret: str = Header(...),
    db: Session = Depends(get_db),
):
    expected_secret = os.getenv("MIGRATION_SECRET", "")
    if not expected_secret or not secrets.compare_digest(x_migration_secret, expected_secret):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if engine.dialect.name != "postgresql":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PostgreSQL required")

    payload = await sqlite_file.read(MAX_SQLITE_BYTES + 1)
    if len(payload) > MAX_SQLITE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large")

    temp_path = None
    source_engine = None
    try:
        with tempfile.NamedTemporaryFile(prefix="finance-tracker-migration-", suffix=".db", delete=False) as temp:
            temp.write(payload)
            temp_path = Path(temp.name)

        source_engine = create_engine(f"sqlite:///{temp_path.as_posix()}")
        with source_engine.connect() as source:
            users = [dict(row._mapping) for row in source.execute(select(User.__table__))]
            transactions = [dict(row._mapping) for row in source.execute(select(Transaction.__table__))]

        for row in users:
            if row.get("created_at") is not None and row["created_at"].tzinfo is None:
                row["created_at"] = row["created_at"].replace(tzinfo=timezone.utc)
        for row in transactions:
            if row.get("date") is not None and row["date"].tzinfo is None:
                row["date"] = row["date"].replace(tzinfo=timezone.utc)

        with db.begin():
            users_in_target = db.execute(text("SELECT count(*) FROM users")).scalar_one()
            transactions_in_target = db.execute(text("SELECT count(*) FROM transactions")).scalar_one()
            if users_in_target or transactions_in_target:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="The destination database is not empty",
                )
            if users:
                db.execute(User.__table__.insert(), users)
            if transactions:
                db.execute(Transaction.__table__.insert(), transactions)
            for table_name in ("users", "transactions"):
                db.execute(
                    text(
                        "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), "
                        f"EXISTS(SELECT 1 FROM {table_name}))"
                    ),
                    {"table_name": table_name},
                )

        return {"users_copied": len(users), "transactions_copied": len(transactions)}
    finally:
        if source_engine is not None:
            source_engine.dispose()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

