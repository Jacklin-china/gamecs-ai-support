# Synthetic RAG smoke check

This is a five-case synthetic smoke test, not a representative quality benchmark. It contains no production records. Human review of answer grounding is still pending, and token use and cost are unavailable.

On 2026-10-30, the configured BGE-M3 embedding and DeepSeek chat services were tested with `cases.json` in an isolated in-memory database. Both runs returned the expected source IDs on all five cases. In the first run the literal fallback detector matched one of four no-source cases; several model-generated responses expressed uncertainty in different words, and some also invoked the ticket tool, so that number is not an independently graded refusal rate.

After changing the no-source branch to return a deterministic refusal (or a deterministic list for explicit requests for the player's own tickets), the second run returned the expected source IDs in five cases and matched the literal refusal on all four no-source cases. The detailed second run is `latest-synthetic-report.json`. This change avoids an unnecessary model call for those four cases; no general accuracy or latency improvement is claimed from this small sample.

On 2026-10-30, `compare_retrieval.py` evaluated whole-document and `chars-600-overlap-100-v1` chunk candidates using the same seven synthetic queries and BGE-M3 embeddings. Each matched the expected source set in 6/7 cases; both missed the activity participation question. The whole-document candidate indexed 3 records and the chunk candidate indexed 5. Details are in `chunk-comparison.json`. The controlled long documents repeat synthetic sections, so these results do not establish real-world performance or justify switching the live index. Answer quality, provider costs and production-scale latency remain unmeasured.
