"""Copy a consistent legacy snapshot into an empty, separate GameCS database."""
import base64
import hashlib
import json
import mimetypes
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import unquote, urlparse
from sqlalchemy import MetaData, Table, func, inspect, select
from sqlalchemy.orm import Session
from app.models import (AnalysisJob, Category, Conversation, HotKeyword, Knowledge, KnowledgeIndexJob, KnowledgeVector,
                        LegacyRecord, Message, Ticket, TicketEvent, TicketImage, User, now)

REQUIRED = ("sys_user", "sys_role", "sys_user_role", "game_category", "ticket_info", "ticket_status_log", "ticket_image", "ticket_ai_result", "knowledge_base", "chat_message", "hot_keyword_daily")


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    return value


def source_rows(conn, name):
    table = Table(name, MetaData(), autoload_with=conn)
    return [dict(row) for row in conn.execute(select(table)).mappings()]


def role_for(keys, user_id):
    if user_id == 1 or "admin" in keys:
        return "admin"
    if "gamecs_admin" in keys:
        return "staff"
    return "player"


def migrate_snapshot(source, target: Session):
    """Copy under an existing consistent, read-only source transaction; commit is caller-owned."""
    names = inspect(source).get_table_names()
    missing = set(REQUIRED) - set(names)
    if missing:
        raise ValueError("Missing source tables: " + ", ".join(sorted(missing)))
    for model in (User, Category, Ticket, TicketEvent, TicketImage, Knowledge, KnowledgeIndexJob, KnowledgeVector, Conversation, Message, AnalysisJob, HotKeyword, LegacyRecord):
        if target.scalar(select(func.count()).select_from(model)):
            raise ValueError("Target must be empty; refusing to overwrite existing records")
    rows = {name: source_rows(source, name) for name in names}

    # Archive every source row, including fields with no equivalent yet.
    inspector = inspect(source)
    for name, values in rows.items():
        pk = inspector.get_pk_constraint(name).get("constrained_columns") or []
        for n, row in enumerate(values):
            payload = {key: json_value(value) for key, value in row.items()}
            key = json.dumps([payload[k] for k in pk], ensure_ascii=False, sort_keys=True) if pk else str(n)
            key = hashlib.sha256(key.encode()).hexdigest()
            target.add(LegacyRecord(source_table=name, source_key=key, payload=payload))
    target.flush()

    role_names = {r["role_id"]: r["role_key"] for r in rows["sys_role"] if r.get("del_flag") != "2" and r.get("status") != "1"}
    user_roles = defaultdict(set)
    for item in rows["sys_user_role"]:
        if item["role_id"] in role_names:
            user_roles[item["user_id"]].add(role_names[item["role_id"]])
    for row in rows["sys_user"]:
        target.add(User(id=row["user_id"], username=row["user_name"], display_name=row.get("nick_name") or row["user_name"], password_hash=row.get("password") or "", role=role_for(user_roles[row["user_id"]], row["user_id"]), active=row.get("status") != "1" and row.get("del_flag") != "2", created_at=row.get("create_time") or now()))
    for row in rows["game_category"]:
        target.add(Category(id=row["id"], parent_id=row["parent_id"], name=row["name"], level=row["level"], active=row.get("status") != "1"))
    target.flush()
    for row in rows["ticket_info"]:
        target.add(Ticket(id=row["id"], ticket_no=row["ticket_no"], game_id=row["game_id"], version_id=row.get("version_id"), kb_version=row.get("kb_version"), category_id=row["category_id"], title=row["title"], content=row["content"], ticket_type=row["ticket_type"], priority=row["priority"], status=row["status"], player_id=row["player_id"], handler_id=row.get("handler_id"), sentiment_score=row.get("sentiment_score"), ai_category=row.get("ai_category"), is_hot=row.get("is_hot") or 0, created_at=row.get("create_time") or now(), updated_at=row.get("update_time") or row.get("create_time") or now(), closed_at=row.get("close_time")))
    for row in rows["knowledge_base"]:
        target.add(Knowledge(id=row["id"], game_id=row["game_id"], title=row["title"], content=row["content"], chunk_index=row.get("chunk_index") or 0, version=row.get("version"), active=row.get("status") != "1", created_at=row.get("create_time") or now()))
    target.flush()
    for row in rows["knowledge_base"]:
        target.add(KnowledgeIndexJob(knowledge_id=row["id"]))
    for row in rows["ticket_status_log"]:
        target.add(TicketEvent(id=row["id"], ticket_id=row["ticket_id"], operator_id=row["operator_id"], from_status=row.get("from_status"), to_status=row["to_status"], remark=row.get("remark") or "", created_at=row.get("create_time") or now()))
    for row in rows["ticket_image"]:
        path = unquote(urlparse(row["image_url"]).path).lstrip("/")
        bucket, sep, key = path.partition("/")
        if not sep or not key:
            raise ValueError(f"Cannot map object reference for legacy image {row['id']}")
        target.add(TicketImage(id=row["id"], ticket_id=row["ticket_id"], bucket=bucket, object_key=key, legacy_url=row["image_url"], original_name=row["image_name"], content_type=mimetypes.guess_type(row["image_name"])[0] or "application/octet-stream", size=row["image_size"], created_at=row.get("create_time") or now()))
    for row in rows["ticket_ai_result"]:
        result = {k: json_value(row.get(k)) for k in ("ai_category", "sentiment_score", "suggest_reply", "similarity_ids", "create_time")}
        target.add(AnalysisJob(ticket_id=row["ticket_id"], status="completed", result=json.dumps(result, ensure_ascii=False)))
    grouped = defaultdict(list)
    for row in rows["chat_message"]:
        grouped[row["conversation_id"]].append(row)
    for cid, messages in grouped.items():
        messages.sort(key=lambda x: x["id"])
        owners = {m["user_id"] for m in messages}
        if len(owners) != 1 or not cid or len(cid) > 128:
            raise ValueError("Ambiguous or invalid legacy conversation identifier")
        title = next((m["content"] for m in messages if m["role"] == "user"), messages[0]["content"])
        target.add(Conversation(id=cid, user_id=messages[0]["user_id"], game_id=None, title=(title or "历史会话")[:200], created_at=messages[0].get("create_time") or now()))
    target.flush()
    for row in rows["chat_message"]:
        target.add(Message(id=row["id"], conversation_id=row["conversation_id"], role=row["role"], content=row["content"], created_at=row.get("create_time") or now()))
    for row in rows["hot_keyword_daily"]:
        target.add(HotKeyword(id=row["id"], rank=row["rank"], keyword=row["keyword"], count=row["count"], heat=str(row["heat"]), trend=str(row["trend"]), description=row.get("description"), stat_date=row["stat_date"], real_date=row.get("real_date"), source_type=row["source_type"], created_at=row.get("create_time") or now()))
    target.flush()
    return {name: len(values) for name, values in rows.items()}
