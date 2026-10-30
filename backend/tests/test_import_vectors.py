import json
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base
from app.models import Knowledge, KnowledgeIndexJob
from scripts import import_legacy_vectors


class FakeRedis:
    def __init__(self): self.values = {}
    def hset(self, key, mapping): self.values[key] = mapping
    def exists(self, key): return key in self.values


def test_imports_only_exact_match_without_reembedding(monkeypatch, tmp_path):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add_all([Knowledge(id=1, game_id=1, title='A', content='原始内容', active=True), Knowledge(id=2, game_id=1, title='B', content='新内容', active=True)])
        db.flush()
        db.add_all([KnowledgeIndexJob(knowledge_id=1), KnowledgeIndexJob(knowledge_id=2)])
        db.commit()
    source = tmp_path / 'vectors.json'
    source.write_text(json.dumps({'one': {'text':'原始内容','metadata':{'gameId':1,'title':'A'},'embedding':[0.5]*1024}, 'two': {'text':'旧内容','metadata':{'gameId':1,'title':'B'},'embedding':[0.5]*1024}},ensure_ascii=False), encoding='utf8')
    fake = FakeRedis()
    config = SimpleNamespace(key_prefix='gamecs_knowledge_v1', index_name='gamecs_knowledge_v1', redis=lambda:fake)
    monkeypatch.setattr(import_legacy_vectors, 'vector_store', lambda:SimpleNamespace(config=config))
    monkeypatch.setattr(import_legacy_vectors, 'SessionLocal', factory)
    monkeypatch.setattr('sys.argv', ['import_legacy_vectors.py', str(source), '--apply'])
    import_legacy_vectors.main()
    assert len(fake.values) == 1
    with factory() as db:
        assert db.get(KnowledgeIndexJob,1).status == 'completed'
        assert db.get(KnowledgeIndexJob,2).status == 'pending'
    engine.dispose()
