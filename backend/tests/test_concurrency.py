"""Concurrency regression with a disposable file database, not a MySQL benchmark."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.db import Base, get_db
from app.main import app
from app.models import Ticket, TicketEvent, User


def test_two_agents_cannot_claim_same_ticket(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'claims.db'}", connect_args={'check_same_thread': False, 'timeout': 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        password = hash_password('password')
        db.add_all([User(id=1, username='player', display_name='player', password_hash=password, role='player'), User(id=2, username='staff1', display_name='staff1', password_hash=password, role='staff'), User(id=3, username='staff2', display_name='staff2', password_hash=password, role='staff')])
        db.add(Ticket(id=1, ticket_no='T-claim', player_id=1, game_id=1, category_id=1, title='Test', content='Test'))
        db.commit()
    def override():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as api:
            tokens = [api.post('/api/v1/auth/login', json={'username':name,'password':'password'}).json()['access_token'] for name in ('staff1','staff2')]
            barrier = Barrier(2)
            def claim(token):
                barrier.wait(timeout=10)
                return api.post('/api/v1/tickets/1/assign', headers={'Authorization':f'Bearer {token}'}).status_code
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(claim, tokens))
            assert sorted(results) == [200, 409]
        with factory() as db:
            assert db.get(Ticket, 1).handler_id in (2, 3)
            assert len(db.scalars(select(TicketEvent).where(TicketEvent.ticket_id == 1, TicketEvent.to_status == 'ASSIGNED')).all()) == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
