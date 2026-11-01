"""Run labelled synthetic retrieval and small end-to-end checks using live providers.

All data is generated for this evaluation; this script never reads the business DB.
Provider requests can incur charges. Run from backend with --live.
"""
import argparse
import json
import math
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ["LOCAL_MODE"] = "true"
os.environ["LOCAL_AUTO_TASKS"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import agent
from app.config import settings
from app.db import Base
from app.models import Knowledge, KnowledgeVector, User


def metric(passed, total):
    return {"passed": passed, "total": total, "percent": round(100 * passed / total, 1) if total else None}


def latency(values):
    ordered = sorted(values)
    if not ordered:
        return None
    return {"p50": round(statistics.median(ordered), 3), "p95": round(ordered[math.ceil(len(ordered) * .95) - 1], 3), "max": round(ordered[-1], 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Use paid external model services")
    parser.add_argument("--output", type=Path, required=True, help="Write synthetic results to this file")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required because this calls external services")
    cfg = settings()
    if not cfg.chat_api_key or not cfg.siliconflow_api_key:
        parser.error("Configure both model and embedding services")
    dataset = json.loads(Path(__file__).with_name("expanded_cases.json").read_text(encoding="utf-8"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    records = []
    answers = []
    # Fixed in advance to keep the end-to-end subset reproducible and affordable.
    answer_ids = {"p01", "p03", "p07", "p11", "p15", "n01", "n05", "n07", "n08", "n09"}
    try:
        with Session(engine, expire_on_commit=False) as db:
            user = User(username="synthetic_eval", display_name="synthetic_eval", password_hash="unused", role="player")
            db.add(user)
            entries = dataset["knowledge"]
            items = [Knowledge(**{k: v for k, v in entry.items() if k != "stale_index"}) for entry in entries]
            db.add_all(items)
            db.flush()
            active = [(entry, item) for entry, item in zip(entries, items) if item.active]
            vectors = agent.embeddings().embed_documents([item.content for _, item in active])
            if len(vectors) != len(active):
                raise ValueError("Embedding count mismatch")
            for (entry, item), vector in zip(active, vectors):
                db.add(KnowledgeVector(knowledge_id=item.id, model=cfg.embedding_model,
                    content_hash="deliberately-stale" if entry.get("stale_index") else agent.document_hash(item), embedding=vector))
            db.commit()
            for case in dataset["cases"]:
                started = time.perf_counter()
                try:
                    sources = agent.retrieve(db, case["game_id"], case["question"])
                    ids = [source["id"] for source in sources]
                    records.append({"id": case["id"], "kind": case["kind"], "expected_ids": case["expected_ids"],
                        "retrieved_ids": ids, "expected_found": set(case["expected_ids"]).issubset(ids) if case["expected_ids"] else None,
                        "exact_match": set(ids) == set(case["expected_ids"]), "seconds": round(time.perf_counter()-started, 3)})
                except Exception as exc:
                    db.rollback()
                    records.append({"id": case["id"], "kind": case["kind"], "error_type": type(exc).__name__, "seconds": round(time.perf_counter()-started, 3)})
                if case["id"] in answer_ids:
                    started = time.perf_counter()
                    try:
                        result = agent.answer(db, user, case["game_id"], case["question"], None)
                        answers.append({"id": case["id"], "kind": case["kind"], "source_ids": [x["id"] for x in result["sources"]],
                            "fallback_detected": "无法可靠回答" in result["answer"], "answer": result["answer"],
                            "seconds": round(time.perf_counter()-started, 3), "human_grounding_review": "pending"})
                    except Exception as exc:
                        db.rollback()
                        answers.append({"id": case["id"], "kind": case["kind"], "error_type": type(exc).__name__, "seconds": round(time.perf_counter()-started, 3)})
    finally:
        engine.dispose()
    positive = [x for x in records if x["kind"] == "answer"]
    negative = [x for x in records if x["kind"] != "answer"]
    completed = [x for x in records if "retrieved_ids" in x]
    answered = [x for x in answers if "answer" in x and x["kind"] == "answer"]
    refused = [x for x in answers if "answer" in x and x["kind"] != "answer"]
    summary = {
        "source_recall_at_5": metric(sum(x.get("expected_found") is True for x in positive), len(positive)),
        "no_source_on_negative": metric(sum(x.get("retrieved_ids") == [] for x in negative), len(negative)),
        "exact_source_set": metric(sum(x.get("exact_match") is True for x in records), len(records)),
        "retrieval_errors": len(records) - len(completed),
        "retrieval_seconds_including_embedding_network": latency([x["seconds"] for x in completed]),
        "answer_end_to_end_seconds": latency([x["seconds"] for x in answered]),
        "negative_end_to_end_seconds": latency([x["seconds"] for x in refused]),
        "negative_fallback_detected": metric(sum(x.get("fallback_detected") is True for x in answers if x["kind"] != "answer"), sum(x["kind"] != "answer" for x in answers)),
        "end_to_end_errors": sum("error_type" in x for x in answers)
    }
    report = {"dataset": dataset["dataset"], "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "database": "isolated SQLite in-memory", "mode": "local single process", "embedding_model": cfg.embedding_model, "chat_model": cfg.chat_model},
        "limitations": ["Synthetic, author-labelled, small sample; no production traffic or independent annotation", "No human grounding review, token usage or cost measurement", "Latency includes external provider calls and is not a load or throughput benchmark", "Errors count as failures in ratios"],
        "summary": summary, "retrieval_results": records, "end_to_end_results": answers}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
