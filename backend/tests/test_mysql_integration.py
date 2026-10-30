"""Opt-in MySQL flow. Only for a freshly migrated local disposable database."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.db import get_db
from app.main import app
from app.models import Category, TicketEvent, User


@pytest.mark.skipif(not os.environ.get('MYSQL_TEST_URL'), reason='set MYSQL_TEST_URL for isolated MySQL integration')
def test_mysql_ticket_flow_and_concurrent_claim():
    url = make_url(os.environ['MYSQL_TEST_URL'])
    if url.host not in ('127.0.0.1', 'localhost') or not (url.database or '').startswith('gamecs_isolated_'):
        pytest.fail('MYSQL_TEST_URL must point to a disposable local database named gamecs_isolated_*')
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        if db.execute(text('SELECT COUNT(*) FROM users')).scalar_one() or db.execute(text('SELECT COUNT(*) FROM categories')).scalar_one():
            pytest.fail('test database is not empty')
        password = hash_password('test-password')
        db.add_all([Category(id=1,parent_id=0,name='Game',level=1),Category(id=2,parent_id=1,name='Version',level=2),Category(id=3,parent_id=2,name='Issue',level=3),User(username='isolated_player',display_name='Player',password_hash=password,role='player'),User(username='isolated_staff1',display_name='Staff 1',password_hash=password,role='staff'),User(username='isolated_staff2',display_name='Staff 2',password_hash=password,role='staff')])
        db.commit()
    def override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as api:
            def auth(name):
                result = api.post('/api/v1/auth/login', json={'username':name,'password':'test-password'})
                assert result.status_code == 200
                return {'Authorization':'Bearer ' + result.json()['access_token']}
            player, staff1, staff2 = map(auth, ('isolated_player','isolated_staff1','isolated_staff2'))
            ticket = api.post('/api/v1/tickets', headers=player, json={'game_id':1,'version_id':2,'category_id':3,'title':'MySQL test','content':'Synthetic'}).json()
            ticket_id = ticket['id']
            barrier = Barrier(2)
            def claim(headers):
                barrier.wait(timeout=10)
                return api.post(f'/api/v1/tickets/{ticket_id}/assign', headers=headers).status_code
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(claim, (staff1, staff2)))
            assert sorted(results) == [200, 409]
            winner = staff1 if results[0] == 200 else staff2
            assert api.post(f'/api/v1/tickets/{ticket_id}/messages', headers=winner, json={'content':'Reply','request_id':'mysql-check'}).status_code == 201
            assert api.post(f'/api/v1/tickets/{ticket_id}/transition', headers=winner, json={'status':'CLOSED','remark':'Resolved'}).status_code == 200
            assert api.post(f'/api/v1/tickets/{ticket_id}/rating', headers=player, json={'score':5,'comment':'Thanks'}).status_code == 201
            with factory() as db:
                assert len(db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == ticket_id, TicketEvent.to_status == 'ASSIGNED')).all()) == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
