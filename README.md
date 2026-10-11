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

## Web Application Screenshots

<img width="1195" height="864" alt="Screenshot 2026-10-11 at 10 31 08 AM" src="https://github.com/user-attachments/assets/ecb0602a-c38e-4a9b-8dea-9e9554cb268e" />
<img width="100%" height="1" alt="" src="https://github.com/user-attachments/assets/f2af28ee-a373-4488-89e5-2b84d5da9620" />
<br><br>

<img width="1151" height="861" alt="Screenshot 2026-10-11 at 10 38 54 AM" src="https://github.com/user-attachments/assets/bf2deec5-393e-4a21-bc1e-ba5630fbc15a" />
<img width="100%" height="1" alt="" src="https://github.com/user-attachments/assets/f2af28ee-a373-4488-89e5-2b84d5da9620" />
<br><br>

<img width="1187" height="857" alt="Screenshot 2026-10-11 at 10 31 45 AM" src="https://github.com/user-attachments/assets/0f855f27-1aeb-4bbc-8e09-46999301fd10" />
<img width="100%" height="1" alt="" src="https://github.com/user-attachments/assets/f2af28ee-a373-4488-89e5-2b84d5da9620" />
<br><br>

<img width="1162" height="862" alt="Screenshot 2026-10-11 at 10 32 21 AM" src="https://github.com/user-attachments/assets/3263b887-6f41-431f-9ca3-7f8c7421179d" />
<img width="100%" height="1" alt="" src="https://github.com/user-attachments/assets/f2af28ee-a373-4488-89e5-2b84d5da9620" />
<br><br>

<img width="1162" height="861" alt="Screenshot 2026-10-11 at 10 31 03 AM" src="https://github.com/user-attachments/assets/8befde50-885f-4336-84cf-8fd0fd8e192b" />
<img width="100%" height="1" alt="" src="https://github.com/user-attachments/assets/f2af28ee-a373-4488-89e5-2b84d5da9620" />
<br><br>

<img width="1177" height="861" alt="Screenshot 2026-10-11 at 10 32 44 AM" src="https://github.com/user-attachments/assets/e5948a4b-3931-46f5-91ad-59ae5f690a05" />





# Hackathon
