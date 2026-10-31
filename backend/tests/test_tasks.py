import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app import tasks
from app.db import Base
from app.models import AnalysisJob, HotKeyword, Knowledge, KnowledgeIndexJob, Ticket, User


@pytest.fixture
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    create_session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionLocal", create_session)
    with create_session() as db:
        db.add(User(id=1, username="p1", display_name="p1", password_hash="not-used", role="player"))
        db.add(Knowledge(id=1, game_id=1, title="规则", content="游戏说明", active=True))
        db.add(Ticket(id=1, ticket_no="T1", player_id=1, game_id=1, category_id=1, title="问题", content="描述"))
        db.flush()
        db.add_all([AnalysisJob(id=1, ticket_id=1), KnowledgeIndexJob(knowledge_id=1)])
        db.commit()
    yield create_session
    engine.dispose()


def test_analysis_is_idempotent(factory, monkeypatch):
    calls = []
    def fake_analysis(content):
        calls.append(content)
        return tasks.Analysis(category="BUG", sentiment_score=2, suggest_reply="请重试")
    monkeypatch.setattr(tasks, "analyze_text", fake_analysis)
    assert tasks.process_analysis.run(1) == "completed"
    assert tasks.process_analysis.run(1) == "already claimed"
    assert len(calls) == 1
    with factory() as db:
        ticket = db.get(Ticket, 1)
        assert ticket.ai_category == "BUG" and ticket.sentiment_score == 2
        assert db.get(AnalysisJob, 1).status == "completed"


def test_failed_analysis_stops_after_three_attempts(factory, monkeypatch):
    def fail(content):
        raise RuntimeError("service unavailable")
    monkeypatch.setattr(tasks, "analyze_text", fail)
    for _ in range(3):
        assert tasks.process_analysis.run(1) == "failed"
    with factory() as db:
        job = db.get(AnalysisJob, 1)
        assert job.status == "failed" and job.attempts == 3
    assert tasks.process_analysis.run(1) == "already claimed"


def test_knowledge_index_job_is_idempotent(factory, monkeypatch):
    from app import agent
    calls = []
    monkeypatch.setattr(agent, "index_item", lambda item: calls.append(item.id))
    assert tasks.process_knowledge.run(1) == "completed"
    assert tasks.process_knowledge.run(1) == "already claimed"
    assert calls == [1]
    with factory() as db:
        assert db.get(KnowledgeIndexJob, 1).status == "completed"


def test_old_failed_analysis_does_not_replace_new_claim(factory, monkeypatch):
    from datetime import timedelta
    def fail_after_reclaim(content):
        with factory() as db:
            job = db.get(AnalysisJob, 1)
            job.started_at += timedelta(seconds=1)
            job.attempts += 1
            db.commit()
        raise RuntimeError("old worker failed")
    monkeypatch.setattr(tasks, "analyze_text", fail_after_reclaim)
    assert tasks.process_analysis.run(1) == "failed"
    with factory() as db:
        job = db.get(AnalysisJob, 1)
        assert job.status == "processing" and job.attempts == 2 and job.error is None


def test_old_failed_index_does_not_replace_new_revision(factory, monkeypatch):
    from app import agent
    def fail_after_edit(item):
        with factory() as db:
            job = db.get(KnowledgeIndexJob, 1)
            job.revision += 1
            job.status = "pending"
            db.commit()
        raise RuntimeError("old index failed")
    monkeypatch.setattr(agent, "index_item", fail_after_edit)
    assert tasks.process_knowledge.run(1) == "failed"
    with factory() as db:
        job = db.get(KnowledgeIndexJob, 1)
        assert job.status == "pending" and job.revision == 2 and job.error is None


def test_keywords_count_each_ticket_once():
    words = dict(tasks.keywords(["闪退闪退", "闪退"] ))
    assert words["闪退"] == 2


def test_hotspot_rebuild_preserves_migrated_statistics(factory):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    with factory() as db:
        db.get(Ticket, 1).content = "闪退闪退"
        db.add(HotKeyword(rank=1, keyword="旧记录", count=1, heat="普通", trend="new", stat_date=today, source_type="legacy"))
        db.commit()
    assert tasks.generate_hotspots.run() > 0
    assert tasks.generate_hotspots.run() > 0
    with factory() as db:
        assert len(db.scalars(select(HotKeyword).where(HotKeyword.source_type == "legacy")).all()) == 1
        assert len(db.scalars(select(HotKeyword).where(HotKeyword.source_type == "live", HotKeyword.keyword == "闪退")).all()) == 1
