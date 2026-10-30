# Synthetic RAG evaluation

`cases.json` contains only invented examples. Run `python evaluation/run.py --live` from the backend directory to call the configured embedding and chat providers against an isolated in-memory database. No real player records are read. The command makes billable external requests; do not run it as part of CI.

The report records source IDs, latency and simple expected-source/refusal checks. These checks are diagnostic, not a substitute for human grading of factual grounding or prompt-injection resilience. Token usage and cost are marked unavailable because the current application does not expose usage. Save reports only after reviewing their contents. Compare versions using the same cases and configuration; five cases are an initial smoke test, not a statistically representative benchmark.

`python evaluation/compare_retrieval.py --live` compares whole-document retrieval with a deterministic versioned chunking candidate on the same synthetic queries. It makes external embedding requests and does not change the production index. Its seven-case corpus includes artificially repeated long documents; expand and independently label representative examples before deciding to deploy chunking.
