"""Read Rows from DuckDB and compute dashboard numbers with the tested money and payoff modules."""

import math
from datetime import date, datetime, timedelta

from . import config, db
from .config import SAVINGS_TRANSFER_PATTERN
from .money import Txn, spending_by_category, summarize
from .payoff import Debt, add_months, compare, simulate


def today() -> date:
    return date.today()


def first_of_next_month(d: date) -> date:
    return add_months(d.replace(day=1), 1)


def month_bounds(d: date) -> tuple[date, date]:
    return d.replace(day=1), first_of_next_month(d) - timedelta(days=1)


def next_occurrence(day: int, as_of: date) -> date:
    """The next date on or after as_of that falls on this day of the month, clamped to short months."""
    def clamp(first: date) -> date:
        return first.replace(day=min(day, month_bounds(first)[1].day))

    this_month = clamp(as_of.replace(day=1))
    return this_month if this_month >= as_of else clamp(first_of_next_month(as_of))


def last_full_month(d: date) -> tuple[date, date]:
    return month_bounds(add_months(d.replace(day=1), -1))


def load_txns(con) -> list[Txn]:
    return [Txn(r["date"], r["amount"], r["category"], r["flow"])
            for r in db.rows(con, "SELECT date, amount, category, flow FROM v_transactions")]


def load_debts(con) -> list[Debt]:
    return [Debt(r["name"], r["balance"], r["apr"], r["min_payment"])
            for r in db.rows(con, "SELECT name, balance, apr, min_payment FROM v_debts WHERE balance > 0")]


def setting(con, key: str, default: str) -> str:
    r = con.execute("SELECT value FROM settings WHERE key = ?", [key]).fetchone()
    return r[0] if r else default


def set_setting(con, key: str, value: str) -> None:
    con.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", [key, value])


def payoff_view(con, as_of: date) -> dict:
    strategy = setting(con, "payoff_strategy", "avalanche")
    extra = float(setting(con, "extra_monthly", "500"))
    order = [n for n in setting(con, "payoff_order", "").split("|") if n] or None
    return payoff_result(con, extra, strategy, order, as_of)


PAYSLIP_TOLERANCE = 0.01


def latest_payslip_net(con) -> float | None:
    """Net pay of the most recent payslip; periods read like 'September 2026'."""
    def when(r) -> date:
        try:
            return datetime.strptime(r["period"], "%B %Y").date()
        except ValueError:
            return date.min

    slips = db.rows(con, "SELECT period, net FROM v_payslips")
    return max(slips, key=when)["net"] if slips else None


def is_duplicate_insight(existing: list[dict], agent: str, title: str) -> bool:
    """True when the user already added or dismissed an Insight with this agent and title."""
    key = " ".join(title.lower().split())
    return any(r["agent"] == agent and r["status"] in ("in_plan", "dismissed") and " ".join(r["title"].lower().split()) == key
               for r in existing)


def dashboard(con, as_of: date | None = None) -> dict:
    as_of = as_of or today()
    txns = load_txns(con)
    m_start, m_end = last_full_month(as_of)
    month = summarize(txns, m_start, m_end)
    d30 = summarize(txns, as_of - timedelta(days=30), as_of)
    prior = [summarize(txns, *month_bounds(add_months(m_start, -i))).spending for i in range(0, 3)]
    avg3 = sum(prior) / 3
    debts = load_debts(con)
    total_debt = sum(d.balance for d in debts)
    avg_apr = sum(d.balance * d.apr for d in debts) / total_debt if total_debt else 0
    payslips = con.execute("SELECT count(*) FROM v_payslips").fetchone()[0]
    payslip_net = latest_payslip_net(con)
    matches_bank = None if payslip_net is None else abs(payslip_net - month.income) <= PAYSLIP_TOLERANCE * month.income
    d30_by_category = spending_by_category(txns, as_of - timedelta(days=30), as_of)
    caps = [{"category": r["category"], "monthly_cap": r["monthly_cap"],
             "last_30_days": d30_by_category.get(r["category"], 0), "over": d30_by_category.get(r["category"], 0) > r["monthly_cap"]}
            for r in db.rows(con, "SELECT category, monthly_cap FROM budget_caps ORDER BY category")]

    cashflow = []
    for i in range(5, -1, -1):
        s, e = month_bounds(add_months(m_start, -i))
        sm = summarize(txns, s, e)
        cashflow.append({"month": s.strftime("%b"), "income": sm.income, "spending": sm.spending,
                         "debt_service": sm.debt_service})

    upcoming = [{"label": f"{r['name']} due", "date": next_occurrence(r["due_day"], as_of)}
                for r in db.rows(con, "SELECT name, due_day FROM v_debts WHERE due_day IS NOT NULL")]
    salary_day = db.rows(con, "SELECT max(date) d FROM v_transactions WHERE flow = 'income'")[0]["d"]
    if salary_day:
        upcoming.append({"label": "Salary credit", "date": next_occurrence(salary_day.day, as_of)})
    upcoming.sort(key=lambda u: u["date"])

    return {
        "as_of": as_of, "month_label": m_start.strftime("%B %Y"),
        "income": {"value": month.income, "payslip_net": payslip_net, "matches_bank": matches_bank,
                   "note": f"{m_start:%b} bank credits · {payslips} payslip{'' if payslips == 1 else 's'}"},
        "spending_30d": {"value": d30.spending, "vs_3mo_avg": (d30.spending / avg3 - 1) if avg3 else None},
        "debt": {"total": total_debt, "accounts": len(debts), "avg_apr": avg_apr},
        "savings_rate": {"value": month.savings_rate, "target": float(setting(con, "savings_target", str(config.SAVINGS_TARGET)))},
        "cashflow": cashflow, "caps": caps,
        "payoff": payoff_view(con, as_of),
        "upcoming": upcoming[:4],
    }


def spend_by_category(con, days: int, as_of: date | None = None) -> dict[str, float]:
    as_of = as_of or today()
    return spending_by_category(load_txns(con), as_of - timedelta(days=days), as_of)


def emergency_fund(con, target_months: float = config.EMERGENCY_FUND_MONTHS, as_of: date | None = None) -> dict:
    as_of = as_of or today()
    saved = -sum(r["amount"] for r in db.rows(con, "SELECT amount FROM v_transactions WHERE description ILIKE ?", [SAVINGS_TRANSFER_PATTERN]))
    s, e = last_full_month(as_of)
    m = summarize(load_txns(con), s, e)
    monthly_need = m.spending + m.debt_service
    covered = saved / monthly_need if monthly_need else 0
    return {"saved": round(saved, 2), "monthly_need": round(monthly_need, 2),
            "months_covered": round(covered, 2), "target_months": target_months,
            "gap": round(max(0, target_months * monthly_need - saved), 2)}


def payoff_comparison(con, extra: float, as_of: date | None = None) -> dict:
    as_of = as_of or today()
    c = compare(load_debts(con), extra, first_of_next_month(as_of))

    def pack(r):
        return {"strategy": r.strategy, "extra_monthly": r.extra, "feasible": r.feasible, "months": r.months,
                "debt_free": r.debt_free, "total_interest": r.total_interest,
                "order": [{"name": n, "paid_off": r.payoff_dates.get(n)} for n in r.order]}

    return {"minimums_only": pack(c.baseline), "avalanche": pack(c.avalanche), "snowball": pack(c.snowball),
            "interest_saved_vs_minimums": c.interest_saved, "months_sooner_vs_minimums": c.months_sooner}


# --- Agent numbers computed by code (agents call these through tools; they never do the arithmetic) ---

DISCRETIONARY = ("Dining out", "Shopping", "Ride-hailing", "Subscriptions", "Entertainment")


def propose_caps(spend_by_category: dict[str, float], discretionary: tuple[str, ...] = DISCRETIONARY,
                 share: float = 0.75) -> list[dict]:
    """Proposed Budget Caps: a discretionary category gets 75% of its last-30-day Spending, rounded to the
    nearest $5 (halves round up). Other categories (rent, groceries, ...) get none. Largest Spending first."""
    out = [{"category": c, "last_30_days": v, "proposed_cap": math.floor(v * share / 5 + 0.5) * 5.0}
           for c, v in spend_by_category.items() if c in discretionary and v > 0]
    return sorted(out, key=lambda r: -r["last_30_days"])


def savings_plan(monthly_surplus: float, saved: float, monthly_need: float, target_months: float,
                 as_of: date | None = None) -> dict:
    """Save the whole monthly surplus until `saved` covers target_months of monthly_need. months_to_goal is
    rounded up; goal_date is the first of the month that many months after as_of's month. No surplus: no date."""
    as_of = as_of or today()
    gap = round(max(0.0, target_months * monthly_need - saved), 2)
    if gap == 0:
        return {"gap": 0.0, "monthly_amount": 0.0, "months_to_goal": 0, "goal_date": as_of}
    if monthly_surplus <= 0:
        return {"gap": gap, "monthly_amount": 0.0, "months_to_goal": None, "goal_date": None}
    months = math.ceil(gap / monthly_surplus)
    return {"gap": gap, "monthly_amount": round(monthly_surplus, 2), "months_to_goal": months,
            "goal_date": add_months(as_of.replace(day=1), months)}


def payoff_result(con, extra: float, strategy: str, order: list[str] | None, as_of: date) -> dict:
    """Simulate a Payoff Strategy over the Debt Accounts; shared by the dashboard and the agents' tool."""
    debts = load_debts(con)
    start = first_of_next_month(as_of)
    res = simulate(debts, extra, strategy, start, order)
    base = simulate(debts, 0, "avalanche", start)
    by_name = {d.name: d for d in debts}
    return {
        "strategy": strategy, "extra_monthly": extra, "feasible": res.feasible,
        "debt_free": res.debt_free, "months": res.months, "total_interest": res.total_interest,
        "interest_saved": round(base.total_interest - res.total_interest, 2),
        "accounts": [{"name": n, "apr": by_name[n].apr, "balance": by_name[n].balance,
                      "paid_off": res.payoff_dates.get(n)} for n in res.order],
    }
