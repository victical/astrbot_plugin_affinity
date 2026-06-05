from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from peewee import DateField, DateTimeField, FloatField, IntegerField, SqliteDatabase
from playhouse.migrate import SqliteMigrator, migrate


DAILY_COUNTER_TABLE = "affinity_daily_counters"
USER_STATE_TABLE = "affinity_user_state"


def _columns(db: SqliteDatabase, table_name: str) -> set[str]:
    return {column.name for column in db.get_columns(table_name)}


def backup_db(db_path: str | Path) -> Path:
    source = Path(db_path)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = source.with_name(f"{source.stem}.optimization-{timestamp}{source.suffix}")
    shutil.copy2(source, backup)
    return backup


def migrate_db(db_path: str | Path, *, backup: bool = True) -> Path | None:
    db_path = Path(db_path)
    backup_path = backup_db(db_path) if backup and db_path.exists() else None

    db = SqliteDatabase(str(db_path))
    db.connect(reuse_if_open=True)
    try:
        migrator = SqliteMigrator(db)
        operations = []

        daily_columns = _columns(db, DAILY_COUNTER_TABLE)
        if "review_negative_delta" not in daily_columns:
            operations.append(
                migrator.add_column(
                    DAILY_COUNTER_TABLE,
                    "review_negative_delta",
                    FloatField(default=0),
                )
            )
        if "last_negative_event_date" not in daily_columns:
            operations.append(
                migrator.add_column(
                    DAILY_COUNTER_TABLE,
                    "last_negative_event_date",
                    DateField(null=True),
                )
            )

        user_columns = _columns(db, USER_STATE_TABLE)
        if "last_demote_at" not in user_columns:
            operations.append(
                migrator.add_column(
                    USER_STATE_TABLE,
                    "last_demote_at",
                    DateTimeField(null=True),
                )
            )
        if "consecutive_interaction_days" not in user_columns:
            operations.append(
                migrator.add_column(
                    USER_STATE_TABLE,
                    "consecutive_interaction_days",
                    IntegerField(default=0),
                )
            )
        if "last_interaction_date" not in user_columns:
            operations.append(
                migrator.add_column(
                    USER_STATE_TABLE,
                    "last_interaction_date",
                    DateField(null=True),
                )
            )

        if operations:
            with db.atomic():
                migrate(*operations)
        return backup_path
    finally:
        if not db.is_closed():
            db.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Add affinity optimization columns to an existing affinity.db."
    )
    parser.add_argument("db_path", help="Path to affinity.db")
    parser.add_argument("--no-backup", action="store_true", help="Skip DB backup")
    args = parser.parse_args()

    created_backup = migrate_db(args.db_path, backup=not args.no_backup)
    if created_backup:
        print(f"backup: {created_backup}")
    print("migration complete")
