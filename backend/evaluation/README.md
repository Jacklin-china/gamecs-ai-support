# Synthetic RAG evaluation

`cases.json` contains only invented examples. Run `python evaluation/run.py --live` from the backend directory to call the configured embedding and chat providers against an isolated in-memory database. No real player records are read. The command makes billable external requests; do not run it as part of CI.

The report records source IDs, latency and simple expected-source/refusal checks. These checks are diagnostic, not a substitute for human grading of factual grounding or prompt-injection resilience. Token usage and cost are marked unavailable because the current application does not expose usage. Save reports only after reviewing their contents. Compare versions using the same cases and configuration; five cases are an initial smoke test, not a statistically representative benchmark.

`python evaluation/compare_retrieval.py --live` compares whole-document retrieval with a deterministic versioned chunking candidate on the same synthetic queries. It makes external embedding requests and does not change the production index. Its seven-case corpus includes artificially repeated long documents; expand and independently label representative examples before deciding to deploy chunking.

An expanded 26-question synthetic evaluation, including the definitions, observed mistakes, timing and limits, is documented in [EXPANDED_REPORT.md](EXPANDED_REPORT.md). Run `python evaluation/expanded_eval.py --live --output evaluation/expanded-report.json` to repeat it. It uses isolated SQLite and paid provider requests, and overwrites the specified report. Source recall and exact source matching are not measures of answer accuracy; synthetic examples and single-run timing cannot establish production quality or a performance improvement.

An AI-assisted, case-by-case preliminary review of the 10 previously recorded answers is in [ANSWER_REVIEW.md](ANSWER_REVIEW.md). It is not an independent human review. In particular, the recorded response for p07 makes a claim about a tool result, but the saved report has no tool trace to verify it.

`challenge_cases.json` adds 16 synthetic questions over the same synthetic knowledge, including paraphrases, cross-game questions, inactive and stale knowledge, and adversarial requests. They are author-labelled, not independently reviewed, and **have not been run**. To evaluate all of them with real providers, run the following from `backend` with isolated configuration; it creates a new report and incurs provider charges:

```powershell
python evaluation/expanded_eval.py --live --dataset evaluation/challenge_cases.json --answer-all --output evaluation/challenge-report.local.json
```

Review any report before sharing it. Model output may include inappropriate or private content if non-synthetic input is used. The `.local.json` output is ignored by Git. Evaluate grounding, completeness and refusal using each case's `review_focus`; source IDs or literal fallback checks alone cannot establish answer correctness.
