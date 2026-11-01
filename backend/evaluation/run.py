"""Run a small synthetic, isolated RAG smoke evaluation against configured providers."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ['LOCAL_MODE'] = 'true'
os.environ['LOCAL_AUTO_TASKS'] = 'false'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app import agent
from app.db import Base
from app.models import Knowledge, KnowledgeVector, User
from app.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Call the configured providers using synthetic data')
    args = parser.parse_args()
    if not args.live:
        parser.error('Pass --live to run paid provider calls on synthetic data')
    if not settings().chat_api_key or not settings().siliconflow_api_key:
        parser.error('Configure both chat and embedding services first')
    cases = json.loads(Path(__file__).with_name('cases.json').read_text(encoding='utf-8'))
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    results = []
    with Session(engine, expire_on_commit=False) as db:
        user = User(username='synthetic_eval', display_name='synthetic_eval', password_hash='unused', role='player')
        db.add(user)
        items = [Knowledge(**entry) for entry in cases['knowledge']]
        db.add_all(items)
        db.flush()
        for item in items:
            if item.active:
                vector = agent.embeddings().embed_documents([item.content])[0]
                db.add(KnowledgeVector(knowledge_id=item.id, model=settings().embedding_model, content_hash=agent.document_hash(item), embedding=vector))
        db.commit()
        for case in cases['cases']:
            started = time.monotonic()
            try:
                result = agent.answer(db, user, case['game_id'], case['question'], None)
                ids = [source['id'] for source in result['sources']]
                results.append({'id':case['id'], 'kind':case['kind'], 'expected_ids':case['expected_ids'], 'retrieved_ids':ids, 'source_match':set(ids)==set(case['expected_ids']), 'refusal_detected':'无法可靠回答' in result['answer'], 'answer':result['answer'], 'seconds':round(time.monotonic()-started, 3), 'tokens':None, 'cost':None, 'human_grounding_review':'pending'})
            except Exception as exc:
                results.append({'id':case['id'], 'error_type':type(exc).__name__, 'seconds':round(time.monotonic()-started, 3)})
    engine.dispose()
    print(json.dumps({'dataset':'synthetic-v1', 'embedding_model':settings().embedding_model, 'chat_model':settings().chat_model, 'results':results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
