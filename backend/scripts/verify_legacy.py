"""Read-only value comparison between legacy and migrated MySQL databases."""
import hashlib
import json
import os
from collections import Counter, defaultdict
from sqlalchemy import create_engine, inspect, select
from app.legacy_migration import json_value, role_for, source_rows
from app.models import AnalysisJob, Category, Conversation, HotKeyword, Knowledge, LegacyRecord, Message, Ticket, TicketEvent, TicketImage, User


def fingerprint(payload):
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_archive(source, target):
    """Compare every source row with the archived values, including tables without primary keys."""
    archive = defaultdict(Counter)
    for table, payload in target.execute(select(LegacyRecord.source_table, LegacyRecord.payload)):
        archive[table][fingerprint(payload)] += 1
    names = set(inspect(source).get_table_names())
    failures = 0
    for name in sorted(names | set(archive)):
        original = Counter(fingerprint({key: json_value(value) for key, value in row.items()}) for row in source_rows(source, name)) if name in names else Counter()
        stored = archive[name]
        missing = sum((original - stored).values())
        extra = sum((stored - original).values())
        print(f"archive {name}: source={sum(original.values())}, missing_or_changed={missing}, extra_or_changed={extra}")
        failures += missing + extra
    return failures


def verify_business_data(source, target):
    """Verify transformed data whose keys or formats differ from the source."""
    failures = 0
    role_names = {r["role_id"]: r["role_key"] for r in source_rows(source, "sys_role") if r.get("del_flag") != "2" and r.get("status") != "1"}
    user_roles = defaultdict(set)
    for row in source_rows(source, "sys_user_role"):
        if row["role_id"] in role_names:
            user_roles[row["user_id"]].add(role_names[row["role_id"]])
    users = {row.id: row for row in target.execute(select(User.__table__)).mappings()}
    bad_users = sum(1 for row in source_rows(source, "sys_user") if row["user_id"] not in users or users[row["user_id"]].role != role_for(user_roles[row["user_id"]], row["user_id"]) or users[row["user_id"]].active != (row.get("status") != "1" and row.get("del_flag") != "2"))
    print(f"mapped roles and account status: mismatches={bad_users}")
    failures += bad_users

    jobs = {row.ticket_id: row for row in target.execute(select(AnalysisJob.__table__)).mappings()}
    bad_jobs = 0
    for row in source_rows(source, "ticket_ai_result"):
        job = jobs.get(row["ticket_id"])
        expected = {key: json_value(row.get(key)) for key in ("ai_category", "sentiment_score", "suggest_reply", "similarity_ids", "create_time")}
        try:
            if not job or job.status != "completed" or json.loads(job.result) != expected:
                bad_jobs += 1
        except (TypeError, ValueError):
            bad_jobs += 1
    print(f"mapped AI analysis: mismatches={bad_jobs}")
    failures += bad_jobs

    conversations = {row.id: row for row in target.execute(select(Conversation.__table__)).mappings()}
    bad_conversations = sum(1 for row in source_rows(source, "chat_message") if row["conversation_id"] not in conversations or conversations[row["conversation_id"]].user_id != row["user_id"])
    print(f"mapped conversation ownership: mismatches={bad_conversations}")
    return failures + bad_conversations


def main():
    src_url, dst_url = os.getenv("LEGACY_DATABASE_URL"), os.getenv("DATABASE_URL")
    if not src_url or not dst_url:
        raise SystemExit("Set LEGACY_DATABASE_URL and DATABASE_URL.")
    src, dst = create_engine(src_url), create_engine(dst_url)
    comparisons = [
        ("sys_user", User, "user_id", {"user_name": "username", "password": "password_hash"}),
        ("game_category", Category, "id", {"parent_id": "parent_id", "name": "name", "level": "level"}),
        ("ticket_info", Ticket, "id", {"ticket_no": "ticket_no", "game_id": "game_id", "version_id": "version_id", "category_id": "category_id", "title": "title", "content": "content", "status": "status", "player_id": "player_id", "handler_id": "handler_id", "sentiment_score": "sentiment_score", "ai_category": "ai_category"}),
        ("ticket_status_log", TicketEvent, "id", {"ticket_id": "ticket_id", "operator_id": "operator_id", "from_status": "from_status", "to_status": "to_status"}),
        ("ticket_image", TicketImage, "id", {"ticket_id": "ticket_id", "image_url": "legacy_url", "image_name": "original_name", "image_size": "size"}),
        ("knowledge_base", Knowledge, "id", {"game_id": "game_id", "title": "title", "content": "content", "chunk_index": "chunk_index"}),
        ("chat_message", Message, "id", {"conversation_id": "conversation_id", "role": "role", "content": "content"}),
        ("hot_keyword_daily", HotKeyword, "id", {"rank": "rank", "keyword": "keyword", "count": "count", "description": "description", "stat_date": "stat_date", "real_date": "real_date", "source_type": "source_type"}),
    ]
    try:
        with src.connect() as a, dst.connect() as b:
            a.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            a.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            b.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            b.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            failures = 0
            for source_name, model, source_id, fields in comparisons:
                old = source_rows(a, source_name)
                new = {row.id: row for row in b.execute(select(model.__table__)).mappings()}
                missing = mismatches = 0
                for row in old:
                    item = new.get(row[source_id])
                    if item is None:
                        missing += 1
                    elif any(row[old_name] != item[new_name] for old_name, new_name in fields.items()):
                        mismatches += 1
                extra = len(new) - (len(old) - missing)
                print(f"{source_name}: source={len(old)}, missing={missing}, changed={mismatches}, extra={extra}")
                failures += missing + mismatches + abs(extra)
            failures += verify_business_data(a, b)
            failures += verify_archive(a, b)
            a.rollback()
            b.rollback()
            if failures:
                raise SystemExit("Verification failed; do not cut over.")
            print("Verification passed for mapped fields and every archived source row.")
    finally:
        src.dispose()
        dst.dispose()


if __name__ == "__main__":
    main()
