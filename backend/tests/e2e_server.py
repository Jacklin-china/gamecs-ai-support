"""Explicitly isolated ephemeral API for the browser acceptance suite only."""
import os
import sys
from pathlib import Path

os.environ.update(LOCAL_MODE='true', LOCAL_AUTO_TASKS='false', RATE_LIMIT_ENABLED='false', DATABASE_URL='sqlite://', JWT_SECRET='isolated-browser-test-secret-at-least-32', CORS_ORIGINS='http://127.0.0.1:5175,http://127.0.0.1:5176', DEEPSEEK_API_KEY='', SILICONFLOW_API_KEY='')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.auth import hash_password
from app.db import Base, get_db
from app.main import app
from app.models import Category, User

engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
factory = sessionmaker(bind=engine, expire_on_commit=False)
with factory() as db:
    password = hash_password('test-password')
    db.add_all([Category(id=1, parent_id=0, name='测试游戏', level=1), Category(id=2, parent_id=1, name='测试版本', level=2), Category(id=3, parent_id=2, name='测试分类', level=3), User(username='e2e_player', display_name='测试玩家', password_hash=password, role='player'), User(username='e2e_staff', display_name='测试客服', password_hash=password, role='staff')])
    db.commit()


def isolated_db():
    with factory() as db:
        yield db


app.dependency_overrides[get_db] = isolated_db

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=18766, log_level='warning')
