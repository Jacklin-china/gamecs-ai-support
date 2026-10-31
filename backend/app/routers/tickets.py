"""Ticket lifecycle and player-staff communication endpoints."""
import uuid
from io import BytesIO
from pathlib import PurePath
from datetime import datetime, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.auth import current_user, staff
from app.config import settings
from app.db import get_db
from app.models import AnalysisJob, AuditEvent, Category, Ticket, TicketEvent, TicketImage, TicketMessage, TicketRating, User, now
from app.schemas import TicketCreate, TicketTransition, TicketMessageInput, TicketRatingInput

router = APIRouter(prefix="/api/v1", tags=["tickets"])
DB = Annotated[Session, Depends(get_db)]
Current = Annotated[User, Depends(current_user)]
Staff = Annotated[User, Depends(staff)]


def ticket_dict(ticket: Ticket):
    return {c.name: getattr(ticket, c.name) for c in Ticket.__table__.columns}


def accessible_ticket(ticket_id: int, db: Session, user: User) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if not ticket or (user.role not in ("staff", "admin") and ticket.player_id != user.id):
        raise HTTPException(404, "工单不存在")
    return ticket


@router.post("/tickets", status_code=201)
def create_ticket(data: TicketCreate, db: DB, user: Current):
    game = db.get(Category, data.game_id)
    category = db.get(Category, data.category_id)
    version = db.get(Category, data.version_id) if data.version_id else None
    if not game or not game.active or game.level != 1 or not category or not category.active or category.level != 3:
        raise HTTPException(422, "请选择有效的游戏和问题分类")
    parent = db.get(Category, category.parent_id)
    if not parent or not parent.active or parent.level != 2 or parent.parent_id != game.id or (data.version_id and (not version or version.id != parent.id)):
        raise HTTPException(422, "游戏、版本和问题分类不匹配")
    ticket = Ticket(**data.model_dump(), player_id=user.id, ticket_no="T" + datetime.now(timezone.utc).strftime("%Y%m%d") + uuid.uuid4().hex[:12].upper())
    db.add(ticket)
    db.flush()
    db.add_all([TicketEvent(ticket_id=ticket.id, operator_id=user.id, to_status="PENDING"), AnalysisJob(ticket_id=ticket.id), AuditEvent(actor_id=user.id, action="ticket.create", target_type="ticket", target_id=ticket.id)])
    db.commit()
    return ticket_dict(ticket)


@router.get("/tickets")
def list_tickets(db: DB, user: Current, scope: str = "mine", limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0), status: str | None = None, query: str = Query(default="", max_length=200), include_total: bool = False):
    statement = select(Ticket)
    if user.role not in ("staff", "admin"):
        statement = statement.where(Ticket.player_id == user.id)
    elif scope == "mine":
        statement = statement.where(Ticket.handler_id == user.id)
    elif scope == "pool":
        statement = statement.where(Ticket.status == "PENDING", Ticket.handler_id.is_(None))
    elif scope != "all":
        raise HTTPException(422, "无效的查询范围")
    if status:
        if status not in {"PENDING", "ASSIGNED", "PROCESSING", "CLOSED", "RATED"}:
            raise HTTPException(422, "无效的工单状态")
        statement = statement.where(Ticket.status == status)
    if query.strip():
        q = query.strip()
        statement = statement.where(Ticket.title.contains(q, autoescape=True) | Ticket.ticket_no.contains(q, autoescape=True))
    total = db.scalar(select(func.count()).select_from(statement.subquery())) if include_total else None
    items = [ticket_dict(x) for x in db.scalars(statement.order_by(Ticket.id.desc()).offset(offset).limit(limit))]
    return {"items": items, "total": total, "limit": limit, "offset": offset} if include_total else items


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: int, db: DB, user: Current):
    ticket = accessible_ticket(ticket_id, db, user)
    events = db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id).order_by(TicketEvent.id)).all()
    images = db.scalars(select(TicketImage).where(TicketImage.ticket_id == ticket.id)).all()
    rating = db.scalar(select(TicketRating).where(TicketRating.ticket_id == ticket_id))
    return {**ticket_dict(ticket), "events": [{"from_status": e.from_status, "to_status": e.to_status, "remark": e.remark, "created_at": e.created_at} for e in events], "images": [{"id": i.id, "name": i.original_name} for i in images], "rating": {"score": rating.score, "comment": rating.comment, "created_at": rating.created_at} if rating else None}


@router.get("/tickets/{ticket_id}/messages")
def ticket_messages(ticket_id: int, db: DB, user: Current, limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    accessible_ticket(ticket_id, db, user)
    rows = db.execute(select(TicketMessage, User.display_name).join(User, User.id == TicketMessage.author_id).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.id.desc()).offset(offset).limit(limit)).all()
    return [{"id": row.id, "author_id": row.author_id, "author_role": row.author_role, "author_name": name, "content": row.content, "created_at": row.created_at} for row, name in rows]


@router.post("/tickets/{ticket_id}/messages", status_code=201)
def create_ticket_message(ticket_id: int, data: TicketMessageInput, db: DB, user: Current):
    accessible_ticket(ticket_id, db, user)
    ticket = db.scalar(select(Ticket).where(Ticket.id == ticket_id).with_for_update())
    content = data.content.strip()
    if not content:
        raise HTTPException(422, "回复内容不能为空")
    def previous():
        return db.scalar(select(TicketMessage).where(TicketMessage.ticket_id == ticket_id, TicketMessage.author_id == user.id, TicketMessage.request_id == data.request_id)) if data.request_id else None
    def response(message):
        if message.content != content:
            raise HTTPException(409, "请求标识已用于其他内容")
        return {"id": message.id, "author_id": user.id, "author_role": message.author_role, "author_name": user.display_name, "content": message.content, "created_at": message.created_at}
    existing = previous()
    if existing:
        return response(existing)
    if ticket.status in ("CLOSED", "RATED"):
        raise HTTPException(409, "工单已结束，不能继续回复")
    if user.role == "staff" and ticket.handler_id != user.id:
        raise HTTPException(403, "仅处理该工单的客服可回复")
    message = TicketMessage(ticket_id=ticket_id, author_id=user.id, author_role=user.role, content=content, request_id=data.request_id)
    db.add(message)
    try:
        db.flush()
        db.add(AuditEvent(actor_id=user.id, action="ticket.message", target_type="ticket", target_id=ticket_id, details={"message_id": message.id}))
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = previous()
        if existing:
            return response(existing)
        raise HTTPException(409, "回复提交冲突，请重试")
    return response(message)


@router.post("/tickets/{ticket_id}/rating", status_code=201)
def rate_ticket(ticket_id: int, data: TicketRatingInput, db: DB, user: Current):
    ticket = accessible_ticket(ticket_id, db, user)
    if ticket.player_id != user.id:
        raise HTTPException(403, "仅工单提交人可评价")
    changed = db.execute(update(Ticket).where(Ticket.id == ticket_id, Ticket.status == "CLOSED").values(status="RATED"))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "工单尚未完成或已评价")
    db.add(TicketRating(ticket_id=ticket_id, player_id=user.id, score=data.score, comment=data.comment.strip()))
    db.add(TicketEvent(ticket_id=ticket_id, operator_id=user.id, from_status="CLOSED", to_status="RATED"))
    db.add(AuditEvent(actor_id=user.id, action="ticket.rating", target_type="ticket", target_id=ticket_id, details={"score": data.score}))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "工单已评价")
    return {"status": "RATED", "score": data.score}


@router.post("/tickets/{ticket_id}/images", status_code=201)
def upload_image(ticket_id: int, db: DB, user: Current, file: Annotated[UploadFile, File()]):
    from app.storage import client, ensure_bucket
    ticket = accessible_ticket(ticket_id, db, user)
    if user.role == "player" and ticket.player_id != user.id:
        raise HTTPException(403, "无权上传附件")
    data = file.file.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "图片不能超过 5 MB")
    signatures = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))
    content_type = next((mime for signature, mime in signatures if data.startswith(signature)), None)
    if not content_type:
        raise HTTPException(422, "仅支持 PNG、JPEG 和 GIF 图片")
    storage = client()
    key = f"tickets/{ticket_id}/{uuid.uuid4().hex}"
    try:
        ensure_bucket(storage, settings().minio_bucket)
        storage.put_object(settings().minio_bucket, key, BytesIO(data), len(data), content_type=content_type)
        image = TicketImage(ticket_id=ticket_id, object_key=key, bucket=settings().minio_bucket, original_name=PurePath((file.filename or "image").replace("\\", "/")).name[:200], content_type=content_type, size=len(data))
        db.add(image)
        db.flush()
        db.add(AuditEvent(actor_id=user.id, action="ticket.upload", target_type="ticket", target_id=ticket_id, details={"image_id":image.id}))
        db.commit()
    except Exception as exc:
        db.rollback()
        try:
            storage.remove_object(settings().minio_bucket, key)
        except Exception:
            pass
        raise HTTPException(503, "附件上传失败") from exc
    return {"id": image.id, "name": image.original_name}


@router.get("/tickets/{ticket_id}/images/{image_id}")
def get_image(ticket_id: int, image_id: int, db: DB, user: Current):
    from app.storage import client
    accessible_ticket(ticket_id, db, user)
    image = db.get(TicketImage, image_id)
    if not image or image.ticket_id != ticket_id:
        raise HTTPException(404, "附件不存在")
    try:
        obj = client().get_object(image.bucket or settings().minio_bucket, image.object_key)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "附件读取失败") from exc
    def chunks():
        try:
            yield from obj.stream(64 * 1024)
        finally:
            obj.close()
            obj.release_conn()
    return StreamingResponse(chunks(), media_type=image.content_type, headers={"X-Content-Type-Options": "nosniff"})


@router.post("/tickets/{ticket_id}/assign")
def assign(ticket_id: int, db: DB, user: Staff):
    result = db.execute(update(Ticket).where(Ticket.id == ticket_id, Ticket.status == "PENDING", Ticket.handler_id.is_(None)).values(status="ASSIGNED", handler_id=user.id))
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "工单不存在或已被接单")
    db.add(TicketEvent(ticket_id=ticket_id, operator_id=user.id, from_status="PENDING", to_status="ASSIGNED"))
    db.add(AuditEvent(actor_id=user.id, action="ticket.assign", target_type="ticket", target_id=ticket_id))
    db.commit()
    return {"status": "ASSIGNED"}


@router.post("/tickets/{ticket_id}/transition")
def transition(ticket_id: int, data: TicketTransition, db: DB, user: Current):
    ticket = accessible_ticket(ticket_id, db, user)
    allowed = {"PENDING": {"CLOSED"}, "ASSIGNED": {"PROCESSING", "CLOSED"}, "PROCESSING": {"CLOSED"}, "CLOSED": {"RATED"}}
    if data.status not in allowed.get(ticket.status, set()):
        raise HTTPException(409, "不允许此状态变更")
    if data.status == "RATED":
        if ticket.player_id != user.id:
            raise HTTPException(403, "仅工单提交人可评价")
    elif user.role not in ("staff", "admin") or (user.role == "staff" and ticket.handler_id != user.id):
        raise HTTPException(403, "无权处理此工单")
    old_status = ticket.status
    values = {"status": data.status}
    if data.status == "CLOSED":
        values["closed_at"] = now()
    changed = db.execute(update(Ticket).where(Ticket.id == ticket.id, Ticket.status == old_status).values(**values))
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(409, "工单状态已变化，请刷新")
    db.add(TicketEvent(ticket_id=ticket.id, operator_id=user.id, from_status=old_status, to_status=data.status, remark=data.remark))
    db.add(AuditEvent(actor_id=user.id, action="ticket.transition", target_type="ticket", target_id=ticket.id, details={"from":old_status,"to":data.status}))
    db.commit()
    return {"status": data.status}
