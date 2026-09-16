# WorkCore maintenance agent

WorkCore AI is a Halifax company building a secure AI workspace for property managers. Agents sit **beside** Yardi, AppFolio, and Buildium. A person reviews anything that matters.

This repo is one of those agents: **maintenance coordination**.

A tenant reports a problem. The model can ask for tools. Tools look up the unit and contractor in a PMS stub, then either create **one** work order, reject, or wait for a human at or above $5000. Retries and two workers still produce one write. `/executions/{id}` reconstructs the run.

That is the job: custom loop (no LangChain), untrusted LLM output, exactly-once side effects, HITL, audit trail, background worker, tenant RLS.

## Run

```bash
python -m uvicorn src.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 — chat the problem in your own words, or attach a photo. The page asks only for what is still missing, then streams the PMS tools and decision. API key `test-key-12345`.

Photos are read when Groq is configured (`GROQ_VISION_MODEL`). Without a key, describe the image in text.

Example: “Heat is out in 4B. Send Acme Corp Supplies, $2500 today.”

If the page errors about a missing column, delete `workcore.db` and restart so SQLite is rebuilt.

Interview / Postgres path: `docker compose up`. See `docs/DATABASE.md`.

Copy `.env.example` to `.env` for Groq. Tests always use the deterministic policy model.

## API

| Method | Path | What |
| --- | --- | --- |
| POST | `/process-request` | Run the agent |
| POST | `/process-request/stream` | Same run, SSE timeline |
| POST | `/chat/stream` | Chat intake, then the same SSE agent loop |
| POST | `/request-jobs` | Queue work; `python -m src.worker` runs it |
| POST | `/executions/{id}/approve` | Human confirm (409 if a second reviewer races) |
| GET | `/executions/{id}` | Tools, inputs, decision |

## Tests

```bash
pytest tests/ -v
python evals/run_cases.py
```

Walkthrough: `docs/INTERVIEW_WALKTHROUGH.md`.
Exactly-once boundary: `docs/adr/001-exactly-once-payments.md`.
