"""Single-process development scheduler for setups without Redis."""
import logging
from threading import Event
from time import monotonic
from sqlalchemy import or_, select, update
from app.config import settings
from app.db import SessionLocal
from app.models import AnalysisJob, KnowledgeIndexJob, now
from app.tasks import generate_hotspots, process_analysis, process_knowledge
from datetime import timedelta

log = logging.getLogger(__name__)


def local_worker(stop: Event):
    next_hotspot = monotonic() + 60
    while not stop.is_set():
        try:
            with SessionLocal() as db:
                stale = now() - timedelta(minutes=10)
                for model in (AnalysisJob, KnowledgeIndexJob):
                    db.execute(update(model).where(model.status == "processing", model.started_at < stale, model.attempts < 3).values(status="pending", started_at=None))
                    db.execute(update(model).where(model.status == "processing", model.started_at < stale, model.attempts >= 3).values(status="failed", error="处理超时"))
                db.execute(update(KnowledgeIndexJob).where(KnowledgeIndexJob.status == "completed", or_(KnowledgeIndexJob.index_backend.is_(None), KnowledgeIndexJob.index_backend != "local")).values(status="pending", attempts=0, revision=KnowledgeIndexJob.revision + 1, error=None))
                db.commit()
                analyses = db.scalars(select(AnalysisJob.id).where(AnalysisJob.status == "pending", AnalysisJob.attempts < 3).order_by(AnalysisJob.id).limit(10)).all() if settings().chat_api_key else []
                vectors = db.scalars(select(KnowledgeIndexJob.knowledge_id).where(KnowledgeIndexJob.status == "pending", KnowledgeIndexJob.attempts < 3).order_by(KnowledgeIndexJob.knowledge_id).limit(10)).all() if settings().siliconflow_api_key else []
            for job_id in analyses:
                if stop.is_set(): break
                process_analysis.run(job_id)
            for item_id in vectors:
                if stop.is_set(): break
                process_knowledge.run(item_id)
            if monotonic() >= next_hotspot and not stop.is_set():
                generate_hotspots.run()
                next_hotspot = monotonic() + 3600
        except Exception:
            log.exception("Local background processing failed")
        stop.wait(10)
