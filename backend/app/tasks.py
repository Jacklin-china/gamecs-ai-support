"""Durable analysis jobs; a periodic sweep repairs lost broker deliveries."""
import json
import re
from collections import Counter
from datetime import date, datetime, time, timedelta
from celery import Celery
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, or_, select, update, delete
from app.config import settings
from app.db import SessionLocal
from app.models import AnalysisJob, HotKeyword, Knowledge, KnowledgeIndexJob, Ticket, now

celery = Celery("gamecs", broker=settings().redis_url)
celery.conf.update(task_serializer="json", accept_content=["json"], timezone="Asia/Shanghai", enable_utc=True,
                   task_acks_late=True, worker_prefetch_multiplier=1,
                   beat_schedule={"enqueue-analysis": {"task": "gamecs.sweep_analysis", "schedule": 30.0},
                                  "enqueue-knowledge": {"task": "gamecs.sweep_knowledge", "schedule": 30.0},
                                  "daily-hotspots": {"task": "gamecs.generate_hotspots", "schedule": 3600.0}})


class Analysis(BaseModel):
    category: str = Field(description="只能是 BUG、ADVICE、COMPLAINT、CONSULT 之一")
    sentiment_score: int = Field(ge=1, le=5)
    suggest_reply: str = Field(max_length=500)


def analyze_text(content: str) -> Analysis:
    from langchain_openai import ChatOpenAI
    cfg = settings()
    if not cfg.chat_api_key:
        raise RuntimeError("AI model is not configured")
    model = ChatOpenAI(api_key=cfg.chat_api_key, base_url=cfg.chat_base_url, model=cfg.chat_model, timeout=30, max_retries=1)
    structured = model.with_structured_output(Analysis, method="json_mode")
    result = structured.invoke([("system", "分析游戏客服工单。返回 JSON 对象，字段 category、sentiment_score、suggest_reply。category 必须为 BUG、ADVICE、COMPLAINT、CONSULT 之一；sentiment_score 为 1 到 5。"), ("user", content)])
    if result.category not in {"BUG", "ADVICE", "COMPLAINT", "CONSULT"}:
        raise ValueError("Invalid category")
    return result


@celery.task(name="gamecs.sweep_analysis")
def sweep_analysis():
    with SessionLocal() as db:
        stale = now() - timedelta(minutes=10)
        db.execute(update(AnalysisJob).where(AnalysisJob.status == "processing", AnalysisJob.started_at < stale, AnalysisJob.attempts < 3).values(status="pending", started_at=None))
        db.execute(update(AnalysisJob).where(AnalysisJob.status == "processing", AnalysisJob.started_at < stale, AnalysisJob.attempts >= 3).values(status="failed", error="处理超时"))
        db.commit()
        job_ids = db.scalars(select(AnalysisJob.id).where(AnalysisJob.status == "pending", AnalysisJob.attempts < 3).order_by(AnalysisJob.id).limit(100)).all()
    for job_id in job_ids:
        process_analysis.delay(job_id)
    return len(job_ids)


@celery.task(name="gamecs.process_analysis")
def process_analysis(job_id: int):
    with SessionLocal() as db:
        result = db.execute(update(AnalysisJob).where(AnalysisJob.id == job_id, AnalysisJob.status == "pending", AnalysisJob.attempts < 3).values(status="processing", started_at=now(), attempts=AnalysisJob.attempts + 1))
        db.commit()
        if result.rowcount != 1:
            return "already claimed"
        job = db.get(AnalysisJob, job_id)
        claimed_at = job.started_at
        ticket = db.get(Ticket, job.ticket_id)
        try:
            if not ticket:
                raise ValueError("工单不存在")
            analysis = analyze_text(ticket.title + "\n" + ticket.content)
            # Ensure a reclaimed job's older worker cannot overwrite newer work.
            updated = db.execute(update(AnalysisJob).where(AnalysisJob.id == job_id, AnalysisJob.status == "processing", AnalysisJob.started_at == claimed_at).values(status="completed", result=analysis.model_dump_json(), error=None))
            if updated.rowcount == 1:
                ticket.ai_category = analysis.category
                ticket.sentiment_score = analysis.sentiment_score
            db.commit()
            return "completed" if updated.rowcount == 1 else "claim expired"
        except Exception:
            db.rollback()
            db.execute(update(AnalysisJob).where(AnalysisJob.id == job_id, AnalysisJob.status == "processing", AnalysisJob.started_at == claimed_at).values(
                status=case((AnalysisJob.attempts >= 3, "failed"), else_="pending"), error="工单分析失败，请检查模型服务配置"))
            db.commit()
            return "failed"


@celery.task(name="gamecs.sweep_knowledge")
def sweep_knowledge():
    if not settings().siliconflow_api_key:
        return 0
    with SessionLocal() as db:
        backend = "local" if settings().local_mode else "redis"
        db.execute(update(KnowledgeIndexJob).where(KnowledgeIndexJob.status == "completed", or_(KnowledgeIndexJob.index_backend.is_(None), KnowledgeIndexJob.index_backend != backend)).values(status="pending", attempts=0, revision=KnowledgeIndexJob.revision + 1, error=None))
        stale = now() - timedelta(minutes=10)
        db.execute(update(KnowledgeIndexJob).where(KnowledgeIndexJob.status == "processing", KnowledgeIndexJob.started_at < stale, KnowledgeIndexJob.attempts < 3).values(status="pending", started_at=None))
        db.execute(update(KnowledgeIndexJob).where(KnowledgeIndexJob.status == "processing", KnowledgeIndexJob.started_at < stale, KnowledgeIndexJob.attempts >= 3).values(status="failed", error="索引更新超时"))
        db.commit()
        ids = db.scalars(select(KnowledgeIndexJob.knowledge_id).where(KnowledgeIndexJob.status == "pending", KnowledgeIndexJob.attempts < 3).limit(100)).all()
    for item_id in ids:
        process_knowledge.delay(item_id)
    return len(ids)


@celery.task(name="gamecs.process_knowledge")
def process_knowledge(item_id: int):
    from app.agent import index_item
    with SessionLocal() as db:
        claimed = db.execute(update(KnowledgeIndexJob).where(KnowledgeIndexJob.knowledge_id == item_id, KnowledgeIndexJob.status == "pending", KnowledgeIndexJob.attempts < 3).values(status="processing", started_at=now(), attempts=KnowledgeIndexJob.attempts + 1))
        db.commit()
        if claimed.rowcount != 1:
            return "already claimed"
        job = db.get(KnowledgeIndexJob, item_id)
        revision, claimed_at = job.revision, job.started_at
        item = db.get(Knowledge, item_id)
        try:
            if not item:
                raise ValueError("Knowledge not found")
            index_item(item)
            completed = db.execute(update(KnowledgeIndexJob).where(KnowledgeIndexJob.knowledge_id == item_id, KnowledgeIndexJob.revision == revision, KnowledgeIndexJob.status == "processing", KnowledgeIndexJob.started_at == claimed_at).values(status="completed", error=None, index_backend="local" if settings().local_mode else "redis"))
            db.commit()
            return "completed" if completed.rowcount == 1 else "claim expired"
        except Exception:
            db.rollback()
            db.execute(update(KnowledgeIndexJob).where(KnowledgeIndexJob.knowledge_id == item_id, KnowledgeIndexJob.revision == revision, KnowledgeIndexJob.status == "processing", KnowledgeIndexJob.started_at == claimed_at).values(
                status=case((KnowledgeIndexJob.attempts >= 3, "failed"), else_="pending"), error="知识索引更新失败，请检查模型和 Redis 服务"))
            db.commit()
            return "failed"


STOP_WORDS = {"我们", "你们", "他们", "这个", "那个", "一个", "什么", "怎么", "问题", "游戏", "客服", "工单", "玩家", "希望", "可以", "没有", "一直", "已经", "出现"}


def keywords(texts: list[str]) -> list[tuple[str, int]]:
    counts = Counter()
    for text in texts:
        words = set()
        for segment in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            for size in (2, 3):
                words.update(segment[i:i + size] for i in range(len(segment) - size + 1))
        counts.update(word for word in words if word not in STOP_WORDS)
    return counts.most_common(20)


@celery.task(name="gamecs.generate_hotspots")
def generate_hotspots():
    # Local business day; a single beat instance should schedule this task.
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    start = datetime.combine(today, time.min)
    with SessionLocal() as db:
        texts = [f"{t.title} {t.content}" for t in db.scalars(select(Ticket).where(Ticket.created_at >= start, Ticket.created_at < start + timedelta(days=1)))]
        words = keywords(texts)
        if not words:
            return 0
        # Rebuild only generated statistics; historical migrated rows are immutable.
        db.execute(delete(HotKeyword).where(HotKeyword.stat_date == today, HotKeyword.source_type == "live"))
        for rank, (word, count) in enumerate(words, 1):
            db.add(HotKeyword(rank=rank, keyword=word, count=count, heat="高" if count >= 10 else "普通", trend="new", description="当日工单关键词统计", stat_date=today, real_date=today, source_type="live"))
        db.commit()
        return len(words)
