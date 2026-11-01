"""Explicitly isolated ephemeral API for the browser acceptance suite only."""
import os
import sys
import uuid
from pathlib import Path

live_ai = os.environ.get('GAMECS_E2E_LIVE_AI') == '1'
provider_env = {key: os.environ.get(key, '') for key in ('DEEPSEEK_API_KEY', 'SILICONFLOW_API_KEY', 'CHAT_PROVIDER')}
os.environ.update(LOCAL_MODE='true', LOCAL_AUTO_TASKS='false', RATE_LIMIT_ENABLED='false', DATABASE_URL='sqlite://', JWT_SECRET='isolated-browser-test-secret-at-least-32', CORS_ORIGINS='http://127.0.0.1:5175,http://127.0.0.1:5176', CHAT_PROVIDER='deepseek', DEEPSEEK_API_KEY='', SILICONFLOW_API_KEY='')
if live_ai:
    os.environ.update({key: value for key, value in provider_env.items() if value})
# This opt-in mode targets a disposable service created by the CI job, never the
# developer's configured storage. A unique bucket prevents test-run collisions.
if os.environ.get('GAMECS_E2E_REAL_STORAGE') == '1':
    os.environ.update(MINIO_ENDPOINT='127.0.0.1:19000', MINIO_ACCESS_KEY='gamecs-e2e-only',
                      MINIO_SECRET_KEY='gamecs-e2e-only-secret', MINIO_SECURE='false',
                      MINIO_BUCKET='gamecs-e2e-' + uuid.uuid4().hex)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.auth import hash_password
from app.db import Base, get_db
from app.main import app
from app.models import Category, User, Knowledge, KnowledgeVector
from app import agent
from app.config import settings
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage


class SyntheticChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


# The default browser suite is simulated. The explicit live mode is billable.
if live_ai:
    if not settings().chat_api_key or not settings().siliconflow_api_key:
        raise RuntimeError('Live AI browser test requires configured chat and embedding keys')
else:
    settings().deepseek_api_key = 'isolated-e2e-only'
    agent.retrieve = lambda db, game_id, question: ([{'id': 1, 'title': '测试知识', 'content': '登录失败请先检查网络。'}] if '登录' in question else [])
    agent.ChatOpenAI = lambda **kwargs: SyntheticChatModel(responses=[AIMessage(content='请先检查网络。')])

engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
factory = sessionmaker(bind=engine, expire_on_commit=False)
with factory() as db:
    password = hash_password('test-password')
    db.add_all([Category(id=1, parent_id=0, name='测试游戏', level=1), Category(id=2, parent_id=1, name='测试版本', level=2), Category(id=3, parent_id=2, name='测试分类', level=3), User(username='e2e_player', display_name='测试玩家', password_hash=password, role='player'), User(username='e2e_staff', display_name='测试客服', password_hash=password, role='staff'), User(username='e2e_admin', display_name='测试管理员', password_hash=password, role='admin'), User(username='e2e_manage', display_name='待管理账号', password_hash=password, role='player')])
    if live_ai:
        knowledge = Knowledge(id=1, game_id=1, title='测试知识', content='登录失败请先检查网络连接，然后重启客户端。', active=True)
        db.add(knowledge)
        db.flush()
        vector = agent.embeddings().embed_documents([knowledge.content])[0]
        db.add(KnowledgeVector(knowledge_id=knowledge.id, model=settings().embedding_model,
                               content_hash=agent.document_hash(knowledge), embedding=vector))
    db.commit()


def isolated_db():
    with factory() as db:
        yield db


app.dependency_overrides[get_db] = isolated_db

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=18766, log_level='warning')
