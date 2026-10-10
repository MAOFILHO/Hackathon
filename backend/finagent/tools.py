"""Tools the specialist agents call. Each returns JSON with the numbers and the Documents they came from."""

import json
import re
from datetime import date
from typing import Literal

from langchain_core.tools import tool

from . import citations, config, db, finance


def _out(result, views: list[str], sources: list[dict] | None = None) -> str:
    return json.dumps({"result": result, "sources": sources or citations.sources_for(db.get(), views)}, default=str)


@tool
def run_sql(query: str) -> str:
    """Run one read-only DuckDB SELECT over the user's data. Views:
    v_transactions(date, description, amount [negative = money out], category, flow [income|spending|transfer|debt_service], needs_review, document, row_no, page [PDF page or null]);
    v_debts(name, kind, balance, apr [0.299 = 29.9%], min_payment, due_day, document, row_no);
    v_payslips(period, employer, gross, net, document, row_no);
    v_budget_caps(category, monthly_cap).
    Spending means flow = 'spending' (refunds are positive spending rows). Returns at most 200 rows."""
    try:
        rows = db.agent_sql(db.get(), query)
    except db.AgentSQLError as e:
        return json.dumps({"error": str(e)})
    return _out(rows, re.findall(r"v_transactions|v_debts|v_payslips", query), citations.sources_from_result(rows))


@tool
def get_debts() -> str:
    """List every Debt Account with balance, APR, minimum payment and due day."""
    return _out(db.rows(db.get(), "SELECT name, kind, balance, apr, min_payment, due_day FROM v_debts"), ["v_debts"])


@tool
def money_summary(start: str, end: str) -> str:
    """Income, Spending, Debt Service, Transfers and Savings Rate between two ISO dates (inclusive)."""
    s = finance.summarize(finance.load_txns(db.get()), date.fromisoformat(start), date.fromisoformat(end))
    return _out(s.__dict__, ["v_transactions"])


@tool
def spending_by_category(days: int = 30) -> str:
    """Spending per category over the last N days, largest first, plus the user's Budget Caps."""
    con = db.get()
    by_cat = sorted(finance.spend_by_category(con, days).items(), key=lambda kv: -kv[1])
    caps = {r["category"]: r["monthly_cap"] for r in db.rows(con, "SELECT * FROM budget_caps")}
    return _out({"days": days, "categories": [{"category": c, "spent": v, "cap": caps.get(c)} for c, v in by_cat]},
                ["v_transactions"])


@tool
def simulate_payoff(extra_monthly: float, strategy: Literal["avalanche", "snowball", "custom"] = "avalanche",
                    custom_order: list[str] | None = None) -> str:
    """Simulate paying off all Debt Accounts with an extra monthly amount on top of minimums.
    Returns debt-free date, months, total interest, interest saved vs minimums and each account's payoff month. Always use this; never estimate."""
    return _out(finance.payoff_result(db.get(), extra_monthly, strategy, custom_order, finance.today()), ["v_debts"])


@tool
def compare_payoff(extra_monthly: float) -> str:
    """Compare minimums-only vs avalanche vs snowball for an extra monthly amount: dates, interest, months saved."""
    return _out(finance.payoff_comparison(db.get(), extra_monthly), ["v_debts"])


@tool
def emergency_fund(target_months: float = config.EMERGENCY_FUND_MONTHS) -> str:
    """Savings set aside (transfers to savings), monthly need (Spending + Debt Service) and months covered."""
    return _out(finance.emergency_fund(db.get(), target_months), ["v_transactions"])


@tool
def debt_cost() -> str:
    """Interest and fees charged per month by each card over the last 3 months (Debt Service rows that are not installments)."""
    rows = db.rows(db.get(), """SELECT strftime(date, '%Y-%m') AS month, description, -sum(amount) AS charged
        FROM v_transactions WHERE flow = 'debt_service' AND regexp_matches(lower(description), 'interest|fee|finance charge')
        AND date >= current_date - INTERVAL 92 DAY GROUP BY ALL ORDER BY month""")
    return _out(rows, ["v_transactions"])


@tool
def propose_caps(days: int = 30) -> str:
    """Proposed monthly Budget Caps from Spending over the last N days: category, last_30_days, proposed_cap.
    Only discretionary categories get a cap (75% of the figure, rounded to $5). Use these caps verbatim."""
    return _out(finance.propose_caps(finance.spend_by_category(db.get(), days)), ["v_transactions"])


@tool
def savings_plan_tool(target_months: float = config.EMERGENCY_FUND_MONTHS) -> str:
    """Monthly auto-save amount (the surplus of the last full month), months to the emergency fund goal and the goal date.
    Always use this; never estimate."""
    con = db.get()
    today = finance.today()
    ef = finance.emergency_fund(con, target_months, today)
    start, end = finance.last_full_month(today)
    m = finance.summarize(finance.load_txns(con), start, end)
    plan = finance.savings_plan(m.income - m.spending - m.debt_service, ef["saved"], ef["monthly_need"], target_months, today)
    return _out({**plan, "saved": ef["saved"], "monthly_need": ef["monthly_need"], "target_months": target_months},
                ["v_transactions"])
