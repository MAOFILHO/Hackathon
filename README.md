# AI Financial Agent

A personal financial coach. 4 specialist agents (Debt Analyzer, Payoff Optimizer, Budget Coach, Savings Strategist)
read the user's own Documents and answer with numbers cited back to their Rows. Vocabulary: see `CONTEXT.md`.

## Run

```bash
# 1. Backend (Python 3.13, uv)
cd backend
cp .env.example .env          # then paste your Anthropic API key into .env
uv sync
uv run uvicorn finagent.api:app --port 8000

# 2. Frontend (Node 20+)
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

The first start loads 5 sample Documents for the demo user Ravi (USD) from `backend/data/samples/`.
Reset them any time with `POST /api/demo/reset` or `uv run python -m finagent.seed`.

Without an API key, the dashboard, payoff slider and CSV/XLSX uploads still work; Ask advisor, insights and
PDF reading need the key. Uploads accept CSV, XLSX and PDF only (the sample receipt is pre-seeded).

## Demo script (about 4 minutes)

1. **Dashboard**: KPIs, cash flow, payoff card. Drag the extra-payment slider; switch Avalanche / Snowball.
2. **Agent insights → Refresh**: 3 agents run; each card cites its Document rows. Click *Set cap* / *Add to plan*.
3. **Ask advisor**: "I can put $1,000 extra toward debt every month. What's the fastest way to be debt-free, and what should I cut?"
   Watch the Route and agent trace stream; then *Apply Payoff Strategy to dashboard* and *Set these Budget Caps*.
4. **Documents**: delete `Card_Activity_Apr-Oct.pdf`, re-upload it from `backend/data/samples/`. Claude reads the PDF live
   (139 rows, about 50 s; a timer shows progress, so talk through what is happening). Fix a *Review* row's category, then *Confirm & index*.
   If a re-upload leaves the card statement duplicated, run `curl -X POST localhost:8000/api/demo/reset` to restore the 5 sample Documents.

## Tests

```bash
cd backend && uv run pytest -q
```

## Layout

- `backend/finagent/money.py`, `payoff.py`: money rules and payoff simulation (plain code, tested; agents never do arithmetic)
- `backend/finagent/schemas.py`: one Pydantic shape per Row type; every model call ends in a `PydanticOutputParser`
- `backend/finagent/ingest.py`: CSV/XLSX parsing, Claude PDF extraction, Haiku categorization
- `backend/finagent/agents.py`, `tools.py`: LangGraph orchestration and the agents' tools (read-only SQL over DuckDB views)
- `frontend/src/pages/`: Dashboard, Advisor, Documents
# Hackathon
