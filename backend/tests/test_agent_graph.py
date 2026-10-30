from contextlib import contextmanager
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from app import agent
from app.db import Base
from app.models import Conversation, Message, Ticket, User


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_graph_tool_only_returns_authenticated_players_tickets(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    saver = InMemorySaver()
    saver.setup = lambda: None
    @contextmanager
    def fake_saver(*args, **kwargs):
        yield saver
    model = ToolModel(responses=[AIMessage(content="", tool_calls=[{"name":"my_tickets", "args":{}, "id":"tool-1", "type":"tool_call"}]), AIMessage(content="你的工单正在处理")])
    monkeypatch.setattr(agent.RedisSaver, "from_conn_string", fake_saver)
    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: model)
    monkeypatch.setattr(agent, "retrieve", lambda *args: [{"id": 42, "title": "游戏规则", "content": "游戏规则资料"}])
    monkeypatch.setattr(agent.settings(), "deepseek_api_key", "test-key")
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([User(id=1, username="p1", display_name="p1", password_hash="x", role="player"), User(id=2, username="p2", display_name="p2", password_hash="x", role="player")])
        db.add_all([Ticket(id=1, ticket_no="T1", player_id=1, game_id=1, category_id=1, title="我的工单", content="内容"), Ticket(id=2, ticket_no="T2", player_id=2, game_id=1, category_id=1, title="别人的秘密", content="内容")])
        db.commit()
        result = agent.answer(db, db.get(User, 1), 1, "查询我的工单", None)
        assert result["answer"] == "你的工单正在处理"
        assert result["sources"] == [{"id": 42, "title": "游戏规则"}]
        cid = result["conversation_id"]
        assert db.get(Conversation, cid).user_id == 1
        messages = db.scalars(select(Message).where(Message.conversation_id == cid).order_by(Message.id)).all()
        assert len(messages) == 2
        assert messages[1].sources == result["sources"]
        checkpoint = saver.get_tuple({"configurable":{"thread_id":f"1:{cid}"}})
        tool_messages = [m for m in checkpoint.checkpoint["channel_values"]["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_messages) == 1
        assert "我的工单" in tool_messages[0].content
        assert "别人的秘密" not in tool_messages[0].content
    engine.dispose()


def test_local_vector_retrieval_respects_game_and_stale_documents(monkeypatch):
    from app.models import Knowledge, KnowledgeVector
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    vector = [1.0] + [0.0] * 1023
    monkeypatch.setattr(agent.settings(), "local_mode", True)
    monkeypatch.setattr(agent, "embeddings", lambda: type("Fake", (), {"embed_query": lambda self, text: vector})())
    with Session(engine) as db:
        items = [Knowledge(id=1, game_id=1, title="a", content="current", active=True), Knowledge(id=2, game_id=2, title="b", content="other game", active=True), Knowledge(id=3, game_id=1, title="c", content="changed", active=True)]
        db.add_all(items)
        db.flush()
        db.add_all([KnowledgeVector(knowledge_id=1, model=agent.settings().embedding_model, content_hash=agent.document_hash(items[0]), embedding=vector), KnowledgeVector(knowledge_id=2, model=agent.settings().embedding_model, content_hash=agent.document_hash(items[1]), embedding=vector), KnowledgeVector(knowledge_id=3, model=agent.settings().embedding_model, content_hash="stale", embedding=vector)])
        db.commit()
        assert [source["id"] for source in agent.retrieve(db, 1, "question")] == [1]
    engine.dispose()


def test_no_knowledge_does_not_return_unsupported_answer(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    model = ToolModel(responses=[AIMessage(content="没有依据的回答")])
    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: model)
    monkeypatch.setattr(agent, "retrieve", lambda *args: [])
    monkeypatch.setattr(agent.settings(), "deepseek_api_key", "test-key")
    monkeypatch.setattr(agent.settings(), "local_mode", True)
    with Session(engine, expire_on_commit=False) as db:
        user = User(id=1, username="p1", display_name="p1", password_hash="x", role="player")
        db.add(user)
        db.commit()
        response = agent.answer(db, user, 1, "我应该怎么办？", None)
        assert "无法可靠回答" in response["answer"]
        assert response["sources"] == []
    engine.dispose()


def test_no_knowledge_ticket_query_only_shows_owners_tickets(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(agent, "retrieve", lambda *args: [])
    monkeypatch.setattr(agent.settings(), "deepseek_api_key", "test-key")
    monkeypatch.setattr(agent.settings(), "local_mode", True)
    with Session(engine, expire_on_commit=False) as db:
        db.add_all([User(id=1, username="p1", display_name="p1", password_hash="x", role="player"), User(id=2, username="p2", display_name="p2", password_hash="x", role="player")])
        db.add_all([Ticket(ticket_no="T1", player_id=1, game_id=1, category_id=1, title="我的问题", content="内容"), Ticket(ticket_no="T2", player_id=2, game_id=1, category_id=1, title="他人秘密", content="内容")])
        db.commit()
        result = agent.answer(db, db.get(User, 1), 1, "查询我的工单", None)
        assert "我的问题" in result["answer"] and "他人秘密" not in result["answer"]
    engine.dispose()
