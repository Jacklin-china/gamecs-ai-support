"""Migrate a read-only MySQL snapshot into an empty, separately provisioned database.

Set LEGACY_DATABASE_URL and DATABASE_URL in the environment. Without --apply,
the entire migration is rehearsed in memory and rolled back.
"""
import argparse
import os
from sqlalchemy import create_engine, event, func, make_url, select
from sqlalchemy.orm import Session
from app.db import Base
from app.legacy_migration import migrate_snapshot
from app.models import Category, Conversation, HotKeyword, Knowledge, LegacyRecord, Message, Ticket, TicketEvent, TicketImage, User, AnalysisJob


def verify(db, source_counts):
    expected = {User: "sys_user", Category: "game_category", Ticket: "ticket_info", TicketEvent: "ticket_status_log", TicketImage: "ticket_image", Knowledge: "knowledge_base", Message: "chat_message", HotKeyword: "hot_keyword_daily", AnalysisJob: "ticket_ai_result"}
    for model, source_table in expected.items():
        actual = db.scalar(select(func.count()).select_from(model))
        if actual != source_counts[source_table]:
            raise ValueError(f"Count mismatch: {source_table}, source={source_counts[source_table]}, target={actual}")
    archived = db.scalar(select(func.count()).select_from(LegacyRecord))
    if archived != sum(source_counts.values()):
        raise ValueError("Archived source row count mismatch")
    return archived, db.scalar(select(func.count()).select_from(Conversation))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write to the separately provisioned target database")
    args = parser.parse_args()
    source_url = os.environ.get("LEGACY_DATABASE_URL")
    if not source_url:
        raise SystemExit("Set LEGACY_DATABASE_URL; credentials are not printed.")
    source_info = make_url(source_url)
    if source_info.get_backend_name() != "mysql" or not source_info.database:
        raise SystemExit("Source must be a MySQL database.")
    if args.apply:
        target_url = os.environ.get("DATABASE_URL")
        if not target_url:
            raise SystemExit("Set DATABASE_URL to the new, empty MySQL database.")
        info = make_url(target_url)
        if info.get_backend_name() != "mysql" or not info.database or info.database.lower() == source_info.database.lower():
            raise SystemExit("Target must be a separate MySQL database with a different database name.")
        target = create_engine(target_url, pool_pre_ping=True)
    else:
        target = create_engine("sqlite://")
        @event.listens_for(target, "connect")
        def enable_foreign_keys(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(target)
    source = create_engine(source_url, pool_pre_ping=True)
    try:
        with source.connect() as conn, Session(target) as db:
            conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conn.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            try:
                counts = migrate_snapshot(conn, db)
                archived, conversations = verify(db, counts)
                if args.apply:
                    db.commit()
                else:
                    db.rollback()
                print(f"{'Migration' if args.apply else 'Rehearsal'} passed: {len(counts)} source tables, {archived} archived rows, {conversations} conversations.")
                for name in ("sys_user", "game_category", "ticket_info", "ticket_status_log", "ticket_image", "ticket_ai_result", "knowledge_base", "chat_message", "hot_keyword_daily"):
                    print(f"{name}: {counts[name]}")
            finally:
                conn.rollback()
    finally:
        source.dispose()
        target.dispose()


if __name__ == "__main__":
    main()
