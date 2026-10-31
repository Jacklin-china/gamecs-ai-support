import os
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "test-secret-at-least-32-characters-long"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base, get_db
from app.main import app
from app.models import Category, User
from app.auth import hash_password


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([Category(id=1, parent_id=0, name="Game", level=1), Category(id=2, parent_id=1, name="Version", level=2), Category(id=3, parent_id=2, name="Issue", level=3), Category(id=4, parent_id=0, name="Retired game", level=1, active=False), User(username="staff", display_name="Staff", password_hash=hash_password("password"), role="staff")])
        db.commit()
    def override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


def login(client, username, password="password"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_registration_ticket_access_and_assignment(client):
    for name in ("player1", "player2"):
        assert client.post("/api/v1/auth/register", json={"username": name, "password": "password"}).status_code == 201
    player = login(client, "player1")
    other = login(client, "player2")
    staff = login(client, "staff")
    payload = {"game_id": 1, "version_id": 2, "category_id": 3, "title": "游戏问题", "content": "无法登录"}
    response = client.post("/api/v1/tickets", headers=player, json=payload)
    assert response.status_code == 201, response.text
    ticket_id = response.json()["id"]
    assert client.get(f"/api/v1/tickets/{ticket_id}", headers=other).status_code == 404
    assert client.post(f"/api/v1/tickets/{ticket_id}/assign", headers=player).status_code == 403
    assert client.post(f"/api/v1/tickets/{ticket_id}/assign", headers=staff).status_code == 200
    assert client.post(f"/api/v1/tickets/{ticket_id}/assign", headers=staff).status_code == 409
    assert client.post(f"/api/v1/tickets/{ticket_id}/transition", headers=staff, json={"status": "PROCESSING"}).status_code == 200
    assert client.post(f"/api/v1/tickets/{ticket_id}/transition", headers=staff, json={"status": "CLOSED"}).status_code == 200
    assert client.post(f"/api/v1/tickets/{ticket_id}/transition", headers=other, json={"status": "RATED"}).status_code == 404
    assert client.post(f"/api/v1/tickets/{ticket_id}/transition", headers=player, json={"status": "RATED"}).status_code == 200


def test_auth_and_category_validation(client):
    assert client.get("/api/v1/tickets").status_code == 401
    assert client.post("/api/v1/auth/register", json={"username": "p1", "password": "password"}).status_code == 201
    headers = login(client, "p1")
    assert client.get("/api/v1/users", headers=headers).status_code == 403
    payload = {"game_id": 1, "version_id": 1, "category_id": 3, "title": "问题", "content": "描述"}
    assert client.post("/api/v1/tickets", headers=headers, json=payload).status_code == 422


def test_request_logging_omits_credentials_and_query(client, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger='gamecs.requests'):
        response = client.post('/api/v1/auth/login?private_query=do-not-log', json={'username':'secret_user','password':'secret_password'})
    assert response.status_code == 401
    assert len(response.headers['X-Request-ID']) == 32
    assert 'secret_user' not in caplog.text and 'secret_password' not in caplog.text and 'private_query' not in caplog.text
    assert 'route=/api/v1/auth/login' in caplog.text


def test_logout_revokes_previously_issued_token(client):
    assert client.post("/api/v1/auth/register", json={"username":"p1","password":"password"}).status_code == 201
    headers = login(client, "p1")
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    assert client.get("/api/v1/auth/me", headers=login(client, "p1")).status_code == 200


def test_knowledge_updates_schedule_index_and_audit(client):
    headers = login(client, "staff")
    data = {"game_id":1,"title":"知识","content":"旧内容","active":True}
    response = client.post("/api/v1/knowledge", headers=headers, json=data)
    assert response.status_code == 201
    assert response.json()["index_status"] == "pending"
    item_id = response.json()["id"]
    data["content"] = "新内容"
    assert client.put(f"/api/v1/knowledge/{item_id}", headers=headers, json=data).json()["index_status"] == "pending"
    assert client.get("/api/v1/audit", headers=headers).status_code == 403


def test_knowledge_rejects_invalid_game_without_breaking_existing_entry(client):
    headers = login(client, "staff")
    data = {"game_id": 1, "title": "知识", "content": "内容", "active": True}
    for invalid_id in (2, 3, 4, 999):
        assert client.post('/api/v1/knowledge', headers=headers, json={**data, "game_id": invalid_id}).status_code == 422
    created = client.post('/api/v1/knowledge', headers=headers, json=data)
    assert created.status_code == 201
    item_id = created.json()['id']
    assert client.put(f'/api/v1/knowledge/{item_id}', headers=headers, json={**data, "game_id": 2}).status_code == 422
    items = client.get('/api/v1/knowledge', headers=headers).json()
    assert items[0]['game_id'] == 1
    sessions = app.dependency_overrides[get_db]()
    db = next(sessions)
    try:
        db.get(Category, 1).active = False
        db.commit()
    finally:
        sessions.close()
    assert client.put(f'/api/v1/knowledge/{item_id}', headers=headers, json={**data, 'active': False}).status_code == 200
    assert client.put(f'/api/v1/knowledge/{item_id}', headers=headers, json=data).status_code == 422


def test_agent_conversations_are_private(client):
    assert client.post("/api/v1/auth/register", json={"username": "p1", "password": "password"}).status_code == 201
    assert client.post("/api/v1/auth/register", json={"username": "p2", "password": "password"}).status_code == 201
    first = login(client, "p1")
    second = login(client, "p2")
    assert client.get("/api/v1/agent/conversations", headers=first).json() == []
    assert client.get("/api/v1/agent/conversations/unknown", headers=second).status_code == 404


def test_ticket_messages_rating_and_server_filter(client):
    for name in ("p1", "p2"):
        assert client.post("/api/v1/auth/register", json={"username": name, "password": "password"}).status_code == 201
    player, other, staff = login(client, "p1"), login(client, "p2"), login(client, "staff")
    payload = {"game_id": 1, "version_id": 2, "category_id": 3, "title": "账号无法登录", "content": "详细描述"}
    ticket = client.post("/api/v1/tickets", headers=player, json=payload).json()
    ticket_id = ticket["id"]
    url = f"/api/v1/tickets/{ticket_id}/messages"
    assert client.get(url, headers=other).status_code == 404
    assert client.post(url, headers=staff, json={"content": "请补充信息"}).status_code == 403
    first = client.post(url, headers=player, json={"content": "补充说明", "request_id": "same-request"})
    assert first.status_code == 201
    assert client.post(url, headers=player, json={"content": "补充说明", "request_id": "same-request"}).json()["id"] == first.json()["id"]
    assert client.post(url, headers=player, json={"content": "不同内容", "request_id": "same-request"}).status_code == 409
    assert client.post(f"/api/v1/tickets/{ticket_id}/assign", headers=staff).status_code == 200
    assert client.post(url, headers=staff, json={"content": "已经处理"}).status_code == 201
    assert [m["content"] for m in client.get(url, headers=player).json()] == ["已经处理", "补充说明"]
    listing = client.get('/api/v1/tickets', headers=player, params={"include_total": True, "status": "ASSIGNED", "query": "登录"}).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == ticket_id
    assert client.get('/api/v1/tickets', headers=other, params={"include_total": True}).json()["total"] == 0
    assert client.post(f"/api/v1/tickets/{ticket_id}/rating", headers=player, json={"score": 5}).status_code == 409
    assert client.post(f"/api/v1/tickets/{ticket_id}/transition", headers=staff, json={"status": "CLOSED", "remark": "问题已解决"}).status_code == 200
    assert client.get(f"/api/v1/tickets/{ticket_id}", headers=player).json()["closed_at"] is not None
    assert client.post(url, headers=player, json={"content": "再次回复"}).status_code == 409
    assert client.post(f"/api/v1/tickets/{ticket_id}/rating", headers=other, json={"score": 5}).status_code == 404
    assert client.post(f"/api/v1/tickets/{ticket_id}/rating", headers=player, json={"score": 5, "comment": "谢谢"}).status_code == 201
    assert client.post(f"/api/v1/tickets/{ticket_id}/rating", headers=player, json={"score": 4}).status_code == 409
    assert client.get(f"/api/v1/tickets/{ticket_id}", headers=player).json()["rating"]["comment"] == "谢谢"


def test_stale_knowledge_is_not_used(monkeypatch):
    from types import SimpleNamespace
    from langchain_core.documents import Document
    from app import agent
    from app.models import Knowledge
    item = Knowledge(id=1, game_id=2, title="title", content="new content", active=True)
    stale = Document(page_content="old content", metadata={"knowledge_id": "1", "content_hash": "old"})
    store = SimpleNamespace(similarity_search_with_score=lambda *args, **kwargs: [(stale, 0.1)])
    monkeypatch.setattr(agent, "vector_store", lambda: store)
    assert agent.retrieve(SimpleNamespace(get=lambda model, key: item), 2, "question") == []


def test_attachment_upload_rejects_non_image_and_hides_other_users(client, monkeypatch):
    from app import storage
    class FakeStorage:
        def bucket_exists(self, *args):
            return True
        def put_object(self, *args, **kwargs):
            pass
        def remove_object(self, *args, **kwargs):
            pass
    monkeypatch.setattr(storage, "client", lambda: FakeStorage())
    for name in ("p1", "p2"):
        assert client.post("/api/v1/auth/register", json={"username": name, "password": "password"}).status_code == 201
    player, other = login(client, "p1"), login(client, "p2")
    response = client.post("/api/v1/tickets", headers=player, json={"game_id":1,"version_id":2,"category_id":3,"title":"问题","content":"描述"})
    ticket_id = response.json()["id"]
    url = f"/api/v1/tickets/{ticket_id}/images"
    assert client.post(url, headers=player, files={"file": ("bad.txt", b"not an image", "image/png")}).status_code == 422
    assert client.post(url, headers=other, files={"file": ("ok.png", b"\x89PNG\r\n\x1a\nimage", "image/png")}).status_code == 404
    response = client.post(url, headers=player, files={"file": ("ok.png", b"\x89PNG\r\n\x1a\nimage", "image/png")})
    assert response.status_code == 201
    assert client.get(f"/api/v1/tickets/{ticket_id}/images/{response.json()['id']}", headers=other).status_code == 404
