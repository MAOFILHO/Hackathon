# AI Financial Agent

A personal financial coach. 4 specialist agents (Debt Analyzer, Payoff Optimizer, Budget Coach, Savings Strategist)
read the user's own Documents and answer with numbers cited back to their Rows. Vocabulary: see `CONTEXT.md`.

## Tech stack

| Layer | Technology | Used for |
|---|---|---|
| Backend | Python 3.13, FastAPI, Uvicorn | REST API and the streamed (server-sent events) Ask advisor answers |
| Agents | LangChain (LCEL) with `langchain-anthropic` | Prompt, model and parser chains; the 4 specialists call tools in a loop |
| Orchestration | LangGraph | Orchestrator picks the Route, runs the specialists, then writes one cited answer |
| Models | Claude Sonnet 5.5, Claude Haiku 5.5 | Sonnet: agents and PDF reading. Haiku: bulk categorizing of Rows |
| Structured output | Pydantic | One schema per Row type (transactions, debts, payslips) and per agent answer |
| Data ("tabular RAG") | DuckDB | Agents write read-only SQL over 4 approved views; answers cite the Rows they used |
| Documents | pandas, openpyxl, Claude PDF reading | CSV, XLSX and PDF ingest |
| Money rules | Plain Python (`money.py`, `payoff.py`) | All arithmetic lives in tested code; the models never calculate |
| Frontend | React 19, TypeScript, Vite | Dashboard, Advisor and Documents pages themed from the mockups in `docs/` |
| Rendering | `react-markdown`, `remark-gfm` | Answers with tables |
| Tests | pytest, httpx | 215 backend tests; the frontend has none yet |
| Deploy | Docker, Azure Container Apps, Bicep | One container behind a shared password; scales to zero |

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

Without an API key, the dashboard, payoff slider and adding CSV/XLSX Documents still work; Ask advisor, insights and
PDF reading need the key. Documents can be CSV, XLSX or PDF (the sample receipt is pre-seeded).

## Demo script (about 4 minutes)

1. **Dashboard**: KPIs, cash flow, payoff card. Drag the extra-payment slider; switch Avalanche / Snowball.
2. **Agent insights → Refresh**: 3 agents run; each card cites its Document rows. Click *Set cap* / *Add to plan*.
3. **Ask advisor**: "I can put $1,000 extra toward debt every month. What's the fastest way to be debt-free, and what should I cut?"
   Watch the Route and agent trace stream; then *Apply Payoff Strategy to dashboard* and *Set these Budget Caps*.
4. **Documents**: delete `Card_Activity_Apr-Oct.pdf`, add it again from `backend/data/samples/`. Claude reads the PDF live
   (139 rows, about 50 s; a timer shows progress, so talk through what is happening). Fix a *Review* row's category, then *Confirm & index*.
   If adding it again leaves the card statement duplicated, run `curl -X POST localhost:8000/api/demo/reset` to restore the 5 sample Documents.

## Deploy to Azure Container Apps (low cost, no portal clicks)

`deploy/azure/` holds a Bicep template and a script. The app runs as one container, scales to zero when nobody
uses it (about $0 idle), and gets an HTTPS link protected by a shared password.

You need: the Azure CLI signed in (`az login`), Docker Desktop running, and a Docker Hub login (`docker login`).

```bash
DOCKERHUB_USER=<your-docker-hub-name> ./deploy/azure/deploy.sh deploy
```

It asks (hidden) for the team password and your Anthropic key, builds the image for `linux/amd64`, pushes it to
Docker Hub (public: it holds the code and sample data, no secrets), creates everything in the resource group
`rg-ai-financial-agent`, and checks the link answers 401 without the password and 200 with it.

- Update after a code change: `DOCKERHUB_USER=<name> ./deploy/azure/deploy.sh update` (secrets are kept).
- Remove everything: `./deploy/azure/deploy.sh destroy`.
- Preview without changing anything: add `DRY_RUN=1`.
- The first visit after a quiet spell takes 10 to 30 s. The database lives in the container: it resets on restart and the 5 sample Documents reload.
- Set a spend limit on your Anthropic workspace. Everyone with the password uses your key.

## Try the container locally

```bash
docker build -t ai-financial-agent .
export ANTHROPIC_API_KEY=<your key>     # inherited by the container, never written on the command line below
docker run --rm -p 7860:7860 -e APP_PASSWORD=choose-a-long-password -e ANTHROPIC_API_KEY ai-financial-agent
```

Open http://localhost:7860 (any user name, the password you chose). The image sets `REQUIRE_PASSWORD=1`:
**without `APP_PASSWORD` the app refuses to start**, so a deployment can never go live open.

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
