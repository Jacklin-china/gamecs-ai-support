"""Import exact matches from the legacy SimpleVectorStore without re-embedding.

Defaults to a read-only match report. --apply needs a configured Redis Stack and
will update only matching knowledge jobs. Unmatched rows remain queued.
"""
import argparse
import json
import math
import struct
from collections import defaultdict
from pathlib import Path
from sqlalchemy import select
from app.agent import document_hash, vector_store
from app.config import settings
from app.db import SessionLocal
from app.models import Knowledge, KnowledgeIndexJob, KnowledgeVector


def load_vectors(path):
    records = json.loads(Path(path).read_text(encoding="utf8"))
    if not isinstance(records, dict):
        raise ValueError("Unsupported legacy vector store format")
    lookup = defaultdict(list)
    for value in records.values():
        meta = value.get("metadata", {})
        vector = value.get("embedding")
        if not isinstance(vector, list) or len(vector) != 1024 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in vector):
            raise ValueError("Invalid legacy embedding vector")
        key = (int(meta.get("gameId", 0)), meta.get("title", ""), value.get("text", ""))
        lookup[key].append(vector)
    return lookup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Path to the original vector-store.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if settings().embedding_model != "BAAI/bge-m3":
        raise SystemExit("Legacy vectors require the original BAAI/bge-m3 embedding model.")
    lookup = load_vectors(args.path)
    matched = skipped = 0
    store = vector_store() if args.apply and not settings().local_mode else None
    with SessionLocal() as db:
        items = db.scalars(select(Knowledge).where(Knowledge.active.is_(True))).all()
        for item in items:
            vectors = lookup.get((item.game_id, item.title, item.content), [])
            if len(vectors) != 1:
                skipped += 1
                continue
            matched += 1
            if not args.apply:
                continue
            # Serialize competing edits and index workers for this knowledge item.
            job = db.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.knowledge_id == item.id).with_for_update())
            if not job or job.status == "processing":
                skipped += 1
                matched -= 1
                db.rollback()
                continue
            db.refresh(item)
            vectors = lookup.get((item.game_id, item.title, item.content), [])
            if len(vectors) != 1 or not item.active:
                skipped += 1
                matched -= 1
                db.rollback()
                continue
            metadata = {"game_id": str(item.game_id), "knowledge_id": str(item.id), "content_hash": document_hash(item)}
            if settings().local_mode:
                row = db.get(KnowledgeVector, item.id)
                if row:
                    row.content_hash, row.model, row.embedding = document_hash(item), settings().embedding_model, vectors[0]
                else:
                    db.add(KnowledgeVector(knowledge_id=item.id, content_hash=document_hash(item), model=settings().embedding_model, embedding=vectors[0]))
            else:
                # Match langchain-redis add_texts' prefix and hash schema.
                key = f"{store.config.key_prefix}:gamecs_knowledge_v1:{item.id}"
                record = {"text": item.content, "embedding": struct.pack("<1024f", *vectors[0]), "_index_name": store.config.index_name, "_metadata_json": json.dumps(metadata), **metadata}
                store.config.redis().hset(key, mapping=record)
                if not store.config.redis().exists(key):
                    raise RuntimeError(f"Index write failed for knowledge {item.id}")
            job.status, job.attempts, job.started_at, job.error = "completed", 0, None, None
            job.index_backend = "local" if settings().local_mode else "redis"
            db.commit()
    print(f"{'Imported' if args.apply else 'Matched'} {matched} exact legacy vectors; {skipped} active knowledge rows remain for embedding.")


if __name__ == "__main__":
    main()
