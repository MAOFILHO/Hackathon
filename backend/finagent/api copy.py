"""FastAPI app. Run: uv run uvicorn finagent.api:app --reload --port 8000"""

import json
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sse_starlette import EventSourceResponse

from . import agents, db, finance, llm, routes_documents, routes_plan, seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    con = db.get()
    if con.execute("SELECT count(*) FROM documents").fetchone()[0] == 0:
        seed.load(con)
    yield


app = FastAPI(title="FinAgent", lifespan=lifespan)
app.include_router(routes_documents.router)
app.include_router(routes_plan.router)


@app.get("/api/health")
def health():
    return {"llm": llm.available(), "models": {"agents": llm.SONNET, "categorize": llm.HAIKU}}


@app.get("/api/dashboard")
def dashboard():
    return jsonable_encoder(finance.dashboard(db.get()))


@app.get("/api/agents")
def agent_status():
    return agents.STATUS


@app.post("/api/demo/reset")
def reset():
    seed.load(db.get())
    return {"reset": datetime.now().isoformat()}


# --- Ask advisor (SSE) ---------------------------------------------------------------------

class Question(BaseModel):
    question: str


@app.post("/api/chat")
def chat(q: Question):
    if not llm.available():
        raise HTTPException(503, "Agents need ANTHROPIC_API_KEY in backend/.env")

    def events():
        try:
            for chunk in agents.graph.stream({"question": q.question}, stream_mode="custom", version="v2"):
                yield {"event": "trace", "data": json.dumps(jsonable_encoder(chunk["data"]))}
        except Exception as e:
            yield {"event": "trace", "data": json.dumps({"type": "error", "message": str(e)[:300]})}
        finally:
            for k in agents.STATUS:
                if agents.STATUS[k] == "running":
                    agents.STATUS[k] = "idle"
        yield {"event": "trace", "data": json.dumps({"type": "end"})}

    return EventSourceResponse(events())
