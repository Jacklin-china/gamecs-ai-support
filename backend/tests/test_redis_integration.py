"""Opt-in Redis Stack integration; only use an empty, disposable CI service."""
import os
import time
from pathlib import Path

import pytest
from celery.contrib.testing.worker import start_worker
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.embeddings import Embeddings
from langgraph.checkpoint.redis import RedisSaver
from langgraph.graph import START, MessagesState, StateGraph
from redis import Redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import agent, rate_limit, tasks
from app.config import settings
from app.db import Base
from app.models import AnalysisJob, Knowledge, Ticket, User


URL = os.environ.get("GAMECS_REDIS_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set GAMECS_REDIS_TEST_URL for an isolated Redis Stack")
_preflight_complete = False


@pytest.fixture
def redis_stack(monkeypatch):
    # The port is intentionally reserved for a disposable CI service. Never run
    # this suite against the application's Redis; index and broker names are real.
    if URL != "redis://127.0.0.1:16391/0":
        pytest.fail("integration test requires isolated Redis at 127.0.0.1:16391")
    global _preflight_complete
    redis = Redis.from_url(URL, socket_connect_timeout=2, socket_timeout=2)
    assert redis.ping()
    if not _preflight_complete:
        if redis.dbsize() or redis.execute_command("FT._LIST"):
            pytest.fail("test Redis is not empty; refusing to change its data")
        _preflight_complete = True
    monkeypatch.setattr(settings(), "redis_url", URL)
    monkeypatch.setattr(settings(), "local_mode", False)
    monkeypatch.setattr(settings(), "siliconflow_api_key", "test-only")
    yield redis
    redis.close()


class SyntheticEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]

    def embed_query(self, text):
        return [1.0] + [0.0] * 1023


def test_vector_index_game_filter_and_stale_detection(redis_stack, monkeypatch):
    import app.db
    monkeypatch.setattr(agent, "OpenAIEmbeddings", lambda **kwargs: SyntheticEmbeddings())
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(app.db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    try:
        with Session(engine, expire_on_commit=False) as db:
            first = Knowledge(id=1, game_id=1, title="first", content="first synthetic document", active=True)
            second = Knowledge(id=2, game_id=2, title="second", content="second synthetic document", active=True)
            db.add_all([first, second])
            db.commit()
            agent.index_item(first)
            agent.index_item(second)
            assert [x["id"] for x in agent.retrieve(db, 1, "first question")] == [1]
            assert [x["id"] for x in agent.retrieve(db, 2, "second question")] == [2]
            first.content = "updated synthetic document"
            db.commit()
            assert agent.retrieve(db, 1, "first question") == []
            agent.index_item(first)
            assert [x["id"] for x in agent.retrieve(db, 1, "first question")] == [1]
            first.active = False
            db.commit()
            agent.index_item(first)
            assert agent.retrieve(db, 1, "first question") == []
    finally:
        engine.dispose()


def test_redis_rate_limit(redis_stack, monkeypatch):
    monkeypatch.setattr(settings(), "rate_limit_enabled", True)
    monkeypatch.setattr(rate_limit, "_redis", None)
    rate_limit.check_limit("integration", "synthetic", 1)
    with pytest.raises(HTTPException) as exc:
        rate_limit.check_limit("integration", "synthetic", 1)
    assert exc.value.status_code == 429


def test_redis_conversation_checkpoint_can_resume_and_delete(redis_stack):
    workflow = StateGraph(MessagesState)
    workflow.add_node("reply", lambda state: {"messages": [AIMessage(content="synthetic response")]})
    workflow.add_edge(START, "reply")
    config = {"configurable": {"thread_id": "gamecs-ci-synthetic-thread"}}
    with RedisSaver.from_conn_string(URL) as saver:
        saver.setup()
        graph = workflow.compile(checkpointer=saver)
        graph.invoke({"messages": [HumanMessage(content="first")]}, config)
        resumed = graph.invoke({"messages": [HumanMessage(content="second")]}, config)
        assert [m.content for m in resumed["messages"]] == ["first", "synthetic response", "second", "synthetic response"]
        saver.delete_thread(config["configurable"]["thread_id"])
        assert saver.get_tuple(config) is None


def test_celery_worker_consumes_redis_delivery(redis_stack, monkeypatch, tmp_path):
    db_path = Path(tmp_path) / "worker.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(tasks, "analyze_text", lambda content: tasks.Analysis(category="BUG", sentiment_score=2, suggest_reply="synthetic"))
    monkeypatch.setattr(tasks.celery.conf, "broker_url", URL)
    completed_deliveries = []
    original_run = tasks.process_analysis.run
    def observed_run(job_id):
        result = original_run(job_id)
        completed_deliveries.append(result)
        return result
    monkeypatch.setattr(tasks.process_analysis, "run", observed_run)
    with factory() as db:
        db.add(User(id=1, username="synthetic", display_name="synthetic", password_hash="unused", role="player"))
        db.add(Ticket(id=1, ticket_no="T-CI", player_id=1, game_id=1, category_id=1, title="test", content="test"))
        db.flush()
        db.add(AnalysisJob(id=1, ticket_id=1))
        db.commit()
    try:
        with start_worker(tasks.celery, pool="solo", concurrency=1, perform_ping_check=False):
            tasks.process_analysis.delay(1)
            tasks.process_analysis.delay(1)  # A duplicate broker delivery must not run the job twice.
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                with factory() as db:
                    job = db.get(AnalysisJob, 1)
                    if job.status == "completed" and len(completed_deliveries) == 2:
                        assert job.attempts == 1
                        assert completed_deliveries == ["completed", "already claimed"]
                        break
                time.sleep(.1)
            else:
                pytest.fail("worker did not complete the job in time")
    finally:
        engine.dispose()
