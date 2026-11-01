"""Opt-in live AI workflow; synthetic data in a disposable local MySQL database."""
import os

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.auth import hash_password
from app.config import settings
from app.db import SessionLocal
from app.main import app
from app.models import Category, KnowledgeIndexJob, User
from app.tasks import process_analysis, process_knowledge


URL = os.environ.get("GAMECS_LIVE_AI_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set GAMECS_LIVE_AI_TEST_URL for isolated paid AI integration")


def test_live_model_knowledge_and_ticket_analysis(monkeypatch):
    import traceback
    from app import agent
    url = make_url(URL)
    if (url.get_backend_name() != "mysql" or url.host not in ("127.0.0.1", "localhost")
            or not (url.database or "").startswith("gamecs_isolated_")
            or make_url(settings().database_url) != url):
        pytest.fail("live test requires its own local gamecs_isolated_* database")
    cfg = settings()
    monkeypatch.setattr(cfg, "chat_provider", os.environ.get("GAMECS_LIVE_CHAT_PROVIDER", "deepseek"))
    if not cfg.chat_api_key or not cfg.siliconflow_api_key:
        pytest.fail("both live model services must be configured")
    monkeypatch.setattr(cfg, "local_mode", True)
    monkeypatch.setattr(cfg, "local_auto_tasks", False)
    monkeypatch.setattr(cfg, "rate_limit_enabled", False)
    failures = []
    original_answer = agent.answer
    def capture_failure(*args, **kwargs):
        try:
            return original_answer(*args, **kwargs)
        except HTTPException as exc:
            cause = exc.__cause__
            if cause:
                frames = traceback.extract_tb(cause.__traceback__)
                failures.append({"type": type(cause).__name__, "status": getattr(cause, "status_code", None), "frames": [f"{frame.filename.rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1]}:{frame.lineno}:{frame.name}" for frame in frames[-4:]]})
            raise
    monkeypatch.setattr(agent, "answer", capture_failure)
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)) or db.scalar(select(Category.id).limit(1)):
            pytest.fail("test database is not empty")
        password = hash_password("isolated-test-password")
        db.add_all([Category(id=1, parent_id=0, name="合成游戏", level=1), Category(id=2, parent_id=1, name="测试版本", level=2), Category(id=3, parent_id=2, name="账号问题", level=3),
                    User(username="synthetic_player", display_name="Player", password_hash=password, role="player"), User(username="synthetic_staff", display_name="Staff", password_hash=password, role="staff")])
        db.commit()
    with TestClient(app) as api:
        def auth(name):
            response = api.post("/api/v1/auth/login", json={"username":name, "password":"isolated-test-password"})
            assert response.status_code == 200
            return {"Authorization":"Bearer " + response.json()["access_token"]}
        player, staff = auth("synthetic_player"), auth("synthetic_staff")
        knowledge = {"game_id":1, "title":"登录排查", "content":"遇到登录问题时，请先确认账号密码，然后检查网络连接。", "active":True}
        created = api.post("/api/v1/knowledge", headers=staff, json=knowledge)
        assert created.status_code == 201
        item_id = created.json()["id"]
        assert process_knowledge.run(item_id) == "completed"
        with SessionLocal() as db:
            assert db.get(KnowledgeIndexJob, item_id).status == "completed"
        question = {"game_id":1, "question":"登录失败怎么排查？"}
        answer = api.post("/api/v1/agent/chat", headers=player, json=question)
        assert answer.status_code == 200, f"{answer.text}; diagnostic={failures}"
        assert any(x["id"] == item_id for x in answer.json()["sources"])
        assert answer.json()["answer"].strip()
        knowledge["content"] = "遇到登录问题时，请检查网络连接，并重启客户端。"
        assert api.put(f"/api/v1/knowledge/{item_id}", headers=staff, json=knowledge).status_code == 200
        stale = api.post("/api/v1/agent/chat", headers=player, json=question)
        assert stale.status_code == 200 and stale.json()["sources"] == []
        assert process_knowledge.run(item_id) == "completed"
        updated = api.post("/api/v1/agent/chat", headers=player, json=question)
        assert updated.status_code == 200 and any(x["id"] == item_id for x in updated.json()["sources"])
        assert api.delete(f"/api/v1/knowledge/{item_id}", headers=staff).status_code == 200
        assert process_knowledge.run(item_id) == "completed"
        inactive = api.post("/api/v1/agent/chat", headers=player, json=question)
        assert inactive.status_code == 200 and inactive.json()["sources"] == []
        ticket = api.post("/api/v1/tickets", headers=player, json={"game_id":1,"version_id":2,"category_id":3,"title":"登录失败","content":"无法登录，请协助检查。"})
        assert ticket.status_code == 201
        from app.models import AnalysisJob
        with SessionLocal() as db:
            job_id = db.scalar(select(AnalysisJob.id).where(AnalysisJob.ticket_id == ticket.json()["id"]))
        assert process_analysis.run(job_id) == "completed"
        result = api.get(f"/api/v1/tickets/{ticket.json()['id']}/analysis", headers=staff)
        assert result.status_code == 200 and result.json()["status"] == "completed"
