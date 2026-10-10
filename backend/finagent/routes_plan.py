"""Insight, Plan, Payoff Strategy and Budget Cap routes."""

import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from . import agents, db, finance, llm
from .payoff import Strategy
from .schemas import CATEGORIES

router = APIRouter(prefix="/api")


# --- Insights, Plan, Budget Caps --------------------------------------------------------

@router.get("/insights")
def insights():
    rows = db.rows(db.get(), "SELECT * FROM insights WHERE status != 'dismissed' ORDER BY created_at DESC")
    for r in rows:
        r["action_payload"], r["sources"] = json.loads(r["action_payload"]), json.loads(r["sources"])
        r["evidence"] = json.loads(r["evidence"] or "[]")
    return jsonable_encoder(rows)


@router.post("/insights/refresh")
def refresh_insights():
    if not llm.available():
        raise HTTPException(503, "Agents need ANTHROPIC_API_KEY in backend/.env")
    agents.generate_insights()
    return insights()


class InsightAction(BaseModel):
    action: Literal["add_to_plan", "dismiss"]


@router.post("/insights/{insight_id}")
def act_on_insight(insight_id: int, body: InsightAction):
    con = db.get()
    found = db.rows(con, "SELECT * FROM insights WHERE id = ?", [insight_id])
    if not found:
        raise HTTPException(404, "Insight not found")
    ins = found[0]
    if body.action == "dismiss":
        con.execute("UPDATE insights SET status = 'dismissed' WHERE id = ?", [insight_id])
        return {"id": insight_id, "status": "dismissed"}
    payload = json.loads(ins["action_payload"])
    cap_set = ins["action_kind"] == "set_cap" and bool(payload.get("category") and payload.get("amount"))
    if cap_set:
        con.execute("INSERT OR REPLACE INTO budget_caps VALUES (?, ?)", [payload["category"], payload["amount"]])
    con.execute("UPDATE insights SET status = 'in_plan' WHERE id = ?", [insight_id])
    return {"id": insight_id, "status": "in_plan", "cap_set": cap_set}


class PayoffChoice(BaseModel):
    strategy: Strategy
    extra_monthly: float
    order: list[str] | None = None


@router.post("/payoff-strategy")
def set_payoff(choice: PayoffChoice):
    con = db.get()
    unknown = set(choice.order or []) - {d.name for d in finance.load_debts(con)}
    if unknown:
        raise HTTPException(422, f"Unknown Debt Accounts in order: {', '.join(sorted(unknown))}")
    finance.set_setting(con, "payoff_strategy", choice.strategy)
    finance.set_setting(con, "extra_monthly", str(max(0.0, choice.extra_monthly)))
    if choice.order is not None or choice.strategy != "custom":  # a slider move sends no order: keep the saved one
        finance.set_setting(con, "payoff_order", "|".join(choice.order or []))
    return jsonable_encoder(finance.payoff_view(con, finance.today()))


class Cap(BaseModel):
    category: Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]
    monthly_cap: float


@router.post("/budget-caps")
def set_caps(caps: list[Cap]):
    con = db.get()
    for c in caps:
        con.execute("INSERT OR REPLACE INTO budget_caps VALUES (?, ?)", [c.category, c.monthly_cap])
    return db.rows(con, "SELECT * FROM budget_caps ORDER BY category")


@router.get("/payoff/compare")
def compare_payoff(extra: float = Query(ge=0)):
    return jsonable_encoder(finance.payoff_comparison(db.get(), extra))
