from io import BytesIO
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base
from app.models import Ticket, TicketImage, User
from scripts import migrate_objects


class Stream(BytesIO):
    def stream(self, chunk_size):
        while chunk := self.read(chunk_size):
            yield chunk
    def release_conn(self):
        pass


class Store:
    def __init__(self):
        self.items = {}
    def stat_object(self, bucket, key):
        return SimpleNamespace(size=len(self.items[(bucket, key)]))
    def get_object(self, bucket, key):
        return Stream(self.items[(bucket, key)])
    def put_object(self, bucket, key, stream, length, **kwargs):
        data = stream.read(length)
        assert len(data) == length
        self.items[(bucket, key)] = data
    def remove_object(self, bucket, key):
        del self.items[(bucket, key)]


def test_copy_is_verified_and_resumable(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread":False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(id=1, username="p1", display_name="p1", password_hash="x"))
        db.add(Ticket(id=1, ticket_no="T1", player_id=1, game_id=1, category_id=1, title="t", content="c"))
        db.flush()
        db.add(TicketImage(id=1, ticket_id=1, object_key="old-key", bucket="old", legacy_url="http://legacy/old/old-key", original_name="a.png", content_type="image/png", size=4))
        db.commit()
    src, dst = Store(), Store()
    src.items[("old", "old-key")] = b"data"
    monkeypatch.setattr(migrate_objects, "SessionLocal", factory)
    monkeypatch.setattr(migrate_objects, "Minio", lambda *a, **kw: src)
    monkeypatch.setattr(migrate_objects, "client", lambda: dst)
    monkeypatch.setattr(migrate_objects, "ensure_bucket", lambda *args: None)
    monkeypatch.setenv("LEGACY_MINIO_ENDPOINT", "legacy")
    monkeypatch.setenv("LEGACY_MINIO_ACCESS_KEY", "key")
    monkeypatch.setenv("LEGACY_MINIO_SECRET_KEY", "secret")
    monkeypatch.setattr("sys.argv", ["migrate_objects.py", "--copy"])
    migrate_objects.main()
    assert len(dst.items) == 1
    migrate_objects.main()
    assert len(dst.items) == 1
    key = next(iter(dst.items))
    dst.items[key] = b"bad"
    with pytest.raises(RuntimeError, match="changed"):
        migrate_objects.main()
    assert src.items[("old", "old-key")] == b"data"
    engine.dispose()
