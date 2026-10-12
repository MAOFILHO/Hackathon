import json
from datetime import date

import pytest

from finagent import config, db, finance, seed, tools

AS_OF = date(2026, 10, 10)


@pytest.fixture()
def con(tmp_path, monkeypatch):
    c = db.connect(tmp_path / "t.duckdb")
    seed.load(c)
    monkeypatch.setattr(db, "get", lambda: c)
    monkeypatch.setattr(finance, "today", lambda: AS_OF)
    return c


def run(tool, **args):
    return json.loads(tool.invoke(args))


# --- pure finance helpers ----------------------------------------------------------------

def test_propose_caps_is_75_percent_of_discretionary_spend_rounded_to_5():
    got = finance.propose_caps({"Dining out": 412.4, "Shopping": 100.0, "Groceries": 600.0, "Rent": 1800.0})
    assert got == [{"category": "Dining out", "last_30_days": 412.4, "proposed_cap": 310.0},
                   {"category": "Shopping", "last_30_days": 100.0, "proposed_cap": 75.0}]


def test_propose_caps_skips_zero_spend_and_rounds_half_up():
    got = finance.propose_caps({"Subscriptions": 10.0, "Entertainment": 0.0})
    assert got == [{"category": "Subscriptions", "last_30_days": 10.0, "proposed_cap": 10.0}]  # 7.5 -> 10


def test_savings_goal_saves_the_surplus_until_the_gap_is_closed():
    p = finance.savings_goal(monthly_surplus=500, saved=3000, monthly_need=2000, target_months=6, as_of=AS_OF)
    assert p["gap"] == 9000 and p["monthly_amount"] == 500 and p["months_to_goal"] == 18
    assert p["goal_date"] == date(2028, 4, 1)


def test_savings_goal_rounds_months_up():
    p = finance.savings_goal(400, 0, 1000, 1, as_of=AS_OF)
    assert p["months_to_goal"] == 3


def test_savings_goal_without_surplus_has_no_date():
    p = finance.savings_goal(-50, 3000, 2000, 6, as_of=AS_OF)
    assert p["monthly_amount"] == 0 and p["months_to_goal"] is None and p["goal_date"] is None


def test_savings_goal_goal_already_met():
    p = finance.savings_goal(500, 12000, 2000, 6, as_of=AS_OF)
    assert p["gap"] == 0 and p["monthly_amount"] == 0 and p["months_to_goal"] == 0 and p["goal_date"] == AS_OF


# --- tools over a seeded tmp database -------------------------------------------------------

def test_get_debts_lists_every_debt_account(con):
    out = run(tools.get_debts)
    assert len(out["result"]) == 4 and sum(d["balance"] for d in out["result"]) == 37500
    assert out["sources"] and out["sources"][0]["document"]


def test_money_summary_matches_finance(con):
    out = run(tools.money_summary, start="2026-09-01", end="2026-09-30")["result"]
    assert out["income"] == 6850 and out["spending"] > 0 and out["savings_rate"] is not None


def test_spending_by_category_is_sorted_and_carries_caps(con):
    con.execute("INSERT INTO budget_caps (category, monthly_cap) VALUES ('Dining out', 300)")
    cats = run(tools.spending_by_category, days=30)["result"]["categories"]
    spent = [c["spent"] for c in cats]
    assert spent == sorted(spent, reverse=True)
    assert {c["category"]: c["cap"] for c in cats}["Dining out"] == 300


def test_run_sql_returns_rows_with_sources(con):
    out = run(tools.run_sql, query="SELECT count(*) AS n FROM v_debts")
    assert out["result"] == [{"n": 4}]


def test_run_sql_blocked_table_returns_error_json(con):
    out = run(tools.run_sql, query="SELECT * FROM documents")
    assert "error" in out and "result" not in out


def test_simulate_payoff_matches_payoff_view(con):
    out = run(tools.simulate_payoff, extra_monthly=500)["result"]
    view = finance.payoff_view(con, AS_OF)
    assert out["strategy"] == "avalanche" and out["extra_monthly"] == 500
    assert out["months"] == view["months"] and out["total_interest"] == view["total_interest"]
    assert [a["name"] for a in out["accounts"]] == [a["name"] for a in view["accounts"]]
    assert out["accounts"][0]["name"].startswith("Visa") and out["accounts"][0]["apr"] == 0.299


def test_simulate_payoff_snowball_starts_with_smallest_balance(con):
    out = run(tools.simulate_payoff, extra_monthly=500, strategy="snowball")["result"]
    assert out["accounts"][0]["name"].startswith("BNPL")


def test_compare_payoff_reports_both_strategies(con):
    out = run(tools.compare_payoff, extra_monthly=500)["result"]
    assert out["avalanche"]["total_interest"] <= out["snowball"]["total_interest"] < out["minimums_only"]["total_interest"]
    assert out["interest_saved_vs_minimums"]["avalanche"] > 0


def test_emergency_fund_counts_transfers_to_savings(con):
    out = run(tools.emergency_fund, target_months=6)["result"]
    assert out["saved"] == 3000 and out["target_months"] == 6


def test_debt_cost_lists_interest_and_fee_rows_only(con):
    out = run(tools.debt_cost)["result"]
    assert out and all(r["charged"] > 0 for r in out)


def test_propose_caps_tool_uses_last_30_days_of_spending(con):
    spent = finance.spend_by_category(con, 30, AS_OF)
    out = run(tools.propose_caps)["result"]
    assert out and all(c["last_30_days"] == spent[c["category"]] for c in out)
    assert {c["category"] for c in out} <= set(config.DISCRETIONARY_CATEGORIES)


def test_savings_goal_uses_surplus_of_the_last_full_month(con):
    s, e = finance.last_full_month(AS_OF)
    m = finance.summarize(finance.load_txns(con), s, e)
    surplus = m.income - m.spending - m.debt_service
    out = run(tools.savings_goal, target_months=6)["result"]
    assert out["monthly_amount"] == round(max(surplus, 0), 2)
    assert out["gap"] == finance.emergency_fund(con, 6, AS_OF)["gap"]


# --- one source of truth for the views and the money constants ----------------------------------------

def test_run_sql_description_lists_every_agent_view_with_its_columns():
    for view, columns in db.AGENT_VIEWS.items():
        assert f"{view}({columns})" in tools.run_sql.description


def test_run_sql_asks_for_citations_of_every_agent_view(con, monkeypatch):
    seen = []
    monkeypatch.setattr(tools.citations, "sources_for", lambda _con, views: seen.append(views) or [])
    out = run(tools.run_sql, query="SELECT category, monthly_cap FROM v_budget_caps")
    assert "error" not in out and seen == [["v_budget_caps"]]


def test_propose_caps_names_its_spending_parameter_spent():
    assert finance.propose_caps(spent={"Dining out": 100.0}) == [
        {"category": "Dining out", "last_30_days": 100.0, "proposed_cap": 75.0}]


def test_cap_rules_come_from_config():
    assert config.CAP_SHARE == 0.75
    assert "Dining out" in config.DISCRETIONARY_CATEGORIES and "Groceries" not in config.DISCRETIONARY_CATEGORIES
    assert not hasattr(finance, "DISCRETIONARY")


def test_the_default_extra_payment_comes_from_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_EXTRA_MONTHLY", 700)
    assert finance.payoff_view(db.connect(tmp_path / "empty.duckdb"), AS_OF)["extra_monthly"] == 700
