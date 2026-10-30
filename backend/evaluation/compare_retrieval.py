"""Compare whole documents and segmented documents on the same synthetic inputs."""
import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent import embeddings
from app.config import settings
from app.knowledge_chunks import CHUNK_VERSION, split_knowledge


def cosine(a, b):
    if len(a) != len(b) or not a or not all(math.isfinite(x) for x in [*a, *b]):
        raise ValueError("invalid embedding")
    norm = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / norm if norm else 0.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.error('Pass --live to call the configured embedding provider on synthetic data')
    if not settings().siliconflow_api_key:
        parser.error('Embedding service is not configured')
    corpus = json.loads(Path(__file__).with_name('retrieval_cases.json').read_text(encoding='utf-8'))
    active = [item for item in corpus['knowledge'] if item['active']]
    service = embeddings()
    # Repeated sections form a controlled long-document scenario; not real user data.
    for item in active:
        item['content'] = '\n'.join(item['sections'] * (8 if item['game_id'] == 1 else 1))
    docs = [(x['id'], x['game_id'], x['content']) for x in active]
    segments = [(x['id'], x['game_id'], c.text) for x in active for c in split_knowledge(x['content'])]
    whole_vectors = service.embed_documents([x[2] for x in docs])
    chunk_vectors = service.embed_documents([x[2] for x in segments])
    if len(whole_vectors) != len(docs) or len(chunk_vectors) != len(segments):
        raise ValueError('embedding provider returned an unexpected count')
    results = []
    for case in corpus['cases']:
        start = time.monotonic()
        query = service.embed_query(case['question'])
        def rank(items, vectors):
            best = {}
            for (id_, game_id, _), vector in zip(items, vectors):
                if game_id != case['game_id']:
                    continue
                score = cosine(query, vector)
                if score >= 0.6:
                    best[id_] = max(best.get(id_, -1), score)
            return [id_ for id_, _ in sorted(best.items(), key=lambda x: (-x[1], x[0]))[:5]]
        whole = rank(docs, whole_vectors)
        chunked = rank(segments, chunk_vectors)
        results.append({'id':case['id'], 'expected_ids':case['expected_ids'], 'whole_ids':whole, 'chunk_ids':chunked, 'whole_match':set(whole)==set(case['expected_ids']), 'chunk_match':set(chunked)==set(case['expected_ids']), 'query_seconds':round(time.monotonic()-start,3)})
    print(json.dumps({'dataset':'synthetic-chunks-v1', 'embedding_model':settings().embedding_model, 'chunk_version':CHUNK_VERSION, 'threshold_cosine':0.6, 'whole_records':len(docs), 'chunk_records':len(segments), 'results':results, 'token_usage':None, 'cost':None}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
