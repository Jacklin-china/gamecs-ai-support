from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from threading import Event, Thread
from typing import Annotated
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.auth import admin, current_user, hash_password, make_token, staff, verify_password
from app.config import settings
from app.db import get_db
from app.models import AnalysisJob, AuditEvent, Category, Conversation, HotKeyword, Knowledge, KnowledgeIndexJob, LegacyRecord, Message, Ticket, User
from app.schemas import AgentQuestion, Credentials, KnowledgeInput, RoleUpdate, UserOut
from app.request_logging import RequestLoggingMiddleware

@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = Event()
    worker = None
    if settings().local_mode and settings().local_auto_tasks:
        from app.local_worker import local_worker
        worker = Thread(target=local_worker, args=(stop,), name="gamecs-background-worker", daemon=True)
        worker.start()
    yield
    stop.set()
    if worker:
        worker.join(timeout=1)


app = FastAPI(title="GameCS API", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings().cors_origins.split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
DB = Annotated[Session, Depends(get_db)]
Current = Annotated[User, Depends(current_user)]
Staff = Annotated[User, Depends(staff)]
Admin = Annotated[User, Depends(admin)]
PREFIX = "/api/v1"


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post(PREFIX + "/auth/register", response_model=UserOut, status_code=201)
def register(data: Credentials, db: DB):
    try:
        password_hash = hash_password(data.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    user = User(username=data.username, display_name=data.username, password_hash=password_hash, role="player")
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "用户名已存在")
    return user


@app.post(PREFIX + "/auth/login")
def login(data: Credentials, db: DB, request: Request):
    from app.rate_limit import check_limit
    check_limit("login", request.client.host if request.client else "unknown", 10)
    user = db.scalar(select(User).where(User.username == data.username))
    if not user or not user.active or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    return {"access_token": make_token(user), "token_type": "bearer", "user": UserOut.model_validate(user)}


@app.get(PREFIX + "/auth/me", response_model=UserOut)
def me(user: Current):
    return user


@app.post(PREFIX + "/auth/logout")
def logout(db: DB, user: Current):
    db.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1))
    db.add(AuditEvent(actor_id=user.id, action="auth.logout", target_type="user", target_id=user.id))
    db.commit()
    return {"logged_out": True}


@app.get(PREFIX + "/users", response_model=list[UserOut])
def users(db: DB, _: Admin, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0), query: str = Query(default="", max_length=64)):
    statement = select(User)
    if query:
        statement = statement.where(User.username.contains(query, autoescape=True))
    return db.scalars(statement.order_by(User.id).offset(offset).limit(limit)).all()


@app.patch(PREFIX + "/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, data: RoleUpdate, db: DB, operator: Admin):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "用户不存在")
    if user.id == operator.id and (data.role != "admin" or not data.active):
        raise HTTPException(400, "不能移除自己的管理员权限")
    old = {"role": user.role, "active": user.active}
    user.role, user.active = data.role, data.active
    db.add(AuditEvent(actor_id=operator.id, action="user.update", target_type="user", target_id=user.id, details={"before":old,"after":{"role":user.role,"active":user.active}}))
    db.commit()
    return user


@app.get(PREFIX + "/audit")
def audit_events(db: DB, _: Admin, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).offset(offset).limit(limit))
    return [{"id": row.id, "actor_id": row.actor_id, "action": row.action, "target_type": row.target_type, "target_id": row.target_id, "details": row.details, "created_at": row.created_at} for row in rows]


@app.get(PREFIX + "/audit/legacy")
def legacy_audit_events(db: DB, _: Admin, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    rows = db.scalars(select(LegacyRecord).where(LegacyRecord.source_table.in_(["op_log", "sys_oper_log"])).order_by(LegacyRecord.id.desc()).offset(offset).limit(limit))
    return [{"source": row.source_table, "record": row.payload} for row in rows]


@app.get(PREFIX + "/categories")
def categories(db: DB, _: Current):
    return [{"id": x.id, "parent_id": x.parent_id, "name": x.name, "level": x.level} for x in db.scalars(select(Category).where(Category.active.is_(True)).order_by(Category.id))]


from app.routers.tickets import router as ticket_router
app.include_router(ticket_router)


@app.get(PREFIX + "/knowledge")
def list_knowledge(db: DB, _: Staff):
    rows = db.execute(select(Knowledge, KnowledgeIndexJob).outerjoin(KnowledgeIndexJob, KnowledgeIndexJob.knowledge_id == Knowledge.id).order_by(Knowledge.id.desc())).all()
    return [{"id": x.id, "game_id": x.game_id, "title": x.title, "content": x.content, "active": x.active, "index_status": job.status if job else "not_queued", "index_revision": job.revision if job else None, "index_error": job.error if job and job.status == "failed" else None} for x, job in rows]


@app.post(PREFIX + "/knowledge", status_code=201)
def create_knowledge(data: KnowledgeInput, db: DB, user: Staff):
    item = Knowledge(**data.model_dump())
    db.add(item)
    db.flush()
    db.add(KnowledgeIndexJob(knowledge_id=item.id))
    db.add(AuditEvent(actor_id=user.id, action="knowledge.create", target_type="knowledge", target_id=item.id))
    db.commit()
    return {"id": item.id, "indexed": False, "index_status": "pending"}


@app.put(PREFIX + "/knowledge/{item_id}")
def update_knowledge(item_id: int, data: KnowledgeInput, db: DB, user: Staff):
    item = db.get(Knowledge, item_id)
    if not item:
        raise HTTPException(404, "知识不存在")
    for key, value in data.model_dump().items():
        setattr(item, key, value)
    _queue_index(db, item.id)
    db.add(AuditEvent(actor_id=user.id, action="knowledge.update", target_type="knowledge", target_id=item.id))
    db.commit()
    return {"id": item.id, "indexed": False, "index_status": "pending"}


@app.delete(PREFIX + "/knowledge/{item_id}")
def deactivate_knowledge(item_id: int, db: DB, user: Staff):
    item = db.get(Knowledge, item_id)
    if not item:
        raise HTTPException(404, "知识不存在")
    item.active = False
    _queue_index(db, item.id)
    db.add(AuditEvent(actor_id=user.id, action="knowledge.deactivate", target_type="knowledge", target_id=item.id))
    db.commit()
    return {"id": item.id, "indexed": False, "index_status": "pending"}


def _queue_index(db: Session, item_id: int):
    result = db.execute(update(KnowledgeIndexJob).where(KnowledgeIndexJob.knowledge_id == item_id).values(revision=KnowledgeIndexJob.revision + 1, status="pending", attempts=0, started_at=None, error=None))
    if result.rowcount == 0:
        db.add(KnowledgeIndexJob(knowledge_id=item_id))


@app.get(PREFIX + "/dashboard")
def dashboard(db: DB, _: Staff):
    counts = dict(db.execute(select(Ticket.status, func.count(Ticket.id)).group_by(Ticket.status)).all())
    return {"total": sum(counts.values()), "by_status": counts}


@app.get(PREFIX + "/dashboard/trend")
def dashboard_trend(db: DB, _: Staff, days: int = Query(default=7, ge=1, le=90)):
    from app.models import now
    today = now().date()
    start = today - timedelta(days=days - 1)
    rows = dict(db.execute(select(func.date(Ticket.created_at), func.count(Ticket.id)).where(Ticket.created_at >= datetime.combine(start, datetime.min.time())).group_by(func.date(Ticket.created_at))).all())
    return [{"date": (start + timedelta(days=i)).isoformat(), "count": rows.get(start + timedelta(days=i), rows.get((start + timedelta(days=i)).isoformat(), 0))} for i in range(days)]


@app.get(PREFIX + "/dashboard/categories")
def dashboard_categories(db: DB, _: Staff):
    return [{"type": key, "count": count} for key, count in db.execute(select(Ticket.ticket_type, func.count(Ticket.id)).group_by(Ticket.ticket_type))]


@app.get(PREFIX + "/dashboard/handlers")
def dashboard_handlers(db: DB, _: Staff):
    rows = db.execute(select(User.id, User.display_name, func.count(Ticket.id)).join(Ticket, Ticket.handler_id == User.id).where(Ticket.status.in_(["CLOSED", "RATED"])).group_by(User.id, User.display_name).order_by(func.count(Ticket.id).desc()).limit(20))
    return [{"user_id": row[0], "name": row[1], "count": row[2]} for row in rows]


@app.get(PREFIX + "/hotspots")
def hotspots(db: DB, _: Current):
    day = db.scalar(select(func.max(HotKeyword.stat_date)))
    if not day:
        return []
    rows = db.scalars(select(HotKeyword).where(HotKeyword.stat_date == day).order_by(HotKeyword.rank)).all()
    return [{"rank": x.rank, "keyword": x.keyword, "count": x.count, "heat": x.heat, "trend": x.trend, "description": x.description, "stat_date": x.stat_date, "real_date": x.real_date, "source_type": x.source_type} for x in rows]


@app.get(PREFIX + "/tickets/{ticket_id}/similar")
def similar_tickets(ticket_id: int, db: DB, _: Staff, limit: int = Query(default=5, ge=1, le=20)):
    from app.tasks import keywords
    target = db.get(Ticket, ticket_id)
    if not target:
        raise HTTPException(404, "工单不存在")
    target_words = {word for word, _ in keywords([target.title + " " + target.content])}
    candidates = db.scalars(select(Ticket).where(Ticket.game_id == target.game_id, Ticket.id != target.id).order_by(Ticket.id.desc()).limit(200)).all()
    matches = []
    for ticket in candidates:
        words = {word for word, _ in keywords([ticket.title + " " + ticket.content])}
        union = target_words | words
        score = len(target_words & words) / len(union) if union else 0
        if score:
            matches.append({"id": ticket.id, "ticket_no": ticket.ticket_no, "title": ticket.title, "status": ticket.status, "score": round(score, 3)})
    return sorted(matches, key=lambda row: row["score"], reverse=True)[:limit]


@app.get(PREFIX + "/tickets/{ticket_id}/analysis")
def ticket_analysis(ticket_id: int, db: DB, _: Staff):
    import json
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.ticket_id == ticket_id))
    if not job:
        raise HTTPException(404, "没有分析记录")
    return {"status": job.status, "result": json.loads(job.result) if job.result else None, "error": job.error}


@app.post(PREFIX + "/tickets/{ticket_id}/analysis/retry")
def retry_analysis(ticket_id: int, db: DB, _: Staff):
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.ticket_id == ticket_id))
    if not job:
        if not db.get(Ticket, ticket_id):
            raise HTTPException(404, "工单不存在")
        job = AnalysisJob(ticket_id=ticket_id)
        db.add(job)
    elif job.status != "failed":
        raise HTTPException(409, "只有失败的任务可以重试")
    job.status, job.attempts, job.error, job.started_at = "pending", 0, None, None
    db.commit()
    return {"status": "pending"}


@app.post(PREFIX + "/agent/chat")
def agent_chat(data: AgentQuestion, db: DB, user: Current):
    from app.agent import answer
    from app.rate_limit import check_limit
    check_limit("agent", str(user.id), 20)
    game = db.get(Category, data.game_id)
    if not game or not game.active or game.level != 1:
        raise HTTPException(422, "请选择有效的游戏")
    return answer(db, user, data.game_id, data.question, data.conversation_id)


@app.get(PREFIX + "/agent/conversations")
def conversations(db: DB, user: Current):
    return [{"id": c.id, "title": c.title, "game_id": c.game_id, "created_at": c.created_at} for c in db.scalars(select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.created_at.desc()))]


@app.get(PREFIX + "/agent/conversations/{conversation_id}")
def conversation_messages(conversation_id: str, db: DB, user: Current):
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    return [{"role": m.role, "content": m.content, "sources": m.sources or [], "created_at": m.created_at} for m in db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id))]


@app.delete(PREFIX + "/agent/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, db: DB, user: Current):
    from sqlalchemy import delete
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(404, "会话不存在")
    if conversation.game_id is not None and not settings().local_mode:
        try:
            from langgraph.checkpoint.redis import RedisSaver
            with RedisSaver.from_conn_string(settings().redis_url) as saver:
                saver.setup()
                saver.delete_thread(f"{user.id}:{conversation_id}")
        except Exception as exc:
            raise HTTPException(503, "会话存储暂不可用") from exc
    db.execute(delete(Message).where(Message.conversation_id == conversation_id))
    db.delete(conversation)
    db.commit()
    return {"deleted": True}


@app.post(PREFIX + "/knowledge/{item_id}/index")
def index_knowledge(item_id: int, db: DB, _: Staff):
    item = db.get(Knowledge, item_id)
    if not item:
        raise HTTPException(404, "知识不存在")
    _queue_index(db, item.id)
    db.commit()
    return {"id": item.id, "status": "pending"}
