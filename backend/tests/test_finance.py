import json
from datetime import date

import pytest

from finagent import citations, db, finance, seed
from finagent.money import summarize

AS_OF = date(2026, 10, 10)


@pytest.fixture()
def con(tmp_path):
    c = db.connect(tmp_path / "f.duckdb")
    seed.load(c)
    return c


def test_dashboard_uses_last_full_month_for_income_and_savings_rate(con):
    d = finance.dashboard(con, AS_OF)
    sep = summarize(finance.load_txns(con), date(2026, 9, 1), date(2026, 9, 30))
    assert d["month_label"] == "September 2026"
    assert d["income"]["value"] == sep.income == 6850
    assert d["savings_rate"]["value"] == pytest.approx((6850 - sep.spending - sep.debt_service) / 6850)


def test_dashboard_spending_30d_and_change_vs_three_month_average(con):
    d = finance.dashboard(con, AS_OF)
    txns = finance.load_txns(con)
    last30 = summarize(txns, date(2026, 9, 11), AS_OF).spending   # 30 days counting both end days: Sep 11 to Oct 10
    months = [summarize(txns, date(2026, m, 1), date(2026, m, 28)).spending for m in (7, 8, 9)]
    assert d["spending_30d"]["value"] == last30
    assert d["spending_30d"]["vs_3mo_avg"] is not None
    assert d["spending_30d"]["vs_3mo_avg"] == pytest.approx(last30 / (sum(months) / 3) - 1, abs=0.05)


def test_dashboard_debt_totals_and_balance_weighted_apr(con):
    d = finance.dashboard(con, AS_OF)["debt"]
    assert d["accounts"] == 4 and d["total"] == 37500
    assert d["avg_apr"] == pytest.approx((9800 * .299 + 14200 * .135 + 12900 * .065) / 37500)


def test_cashflow_covers_six_months_ending_with_last_full_month(con):
    cash = finance.dashboard(con, AS_OF)["cashflow"]
    assert [c["month"] for c in cash] == ["Apr", "May", "Jun", "Jul", "Aug", "Sep"]
    assert all(c["income"] == 6850 for c in cash)


def test_upcoming_lists_due_dates_in_order(con):
    up = finance.dashboard(con, AS_OF)["upcoming"]
    assert [u["date"] for u in up] == sorted(u["date"] for u in up)
    assert up[0]["label"].startswith("Visa") and up[0]["date"] == date(2026, 10, 14)
    assert {"Salary credit"} <= {u["label"] for u in up}


@pytest.mark.parametrize("day, as_of, expected", [
    (14, date(2026, 10, 14), date(2026, 10, 14)),   # due today counts
    (1, date(2026, 10, 14), date(2026, 11, 1)),     # already passed: next month
    (31, date(2026, 11, 5), date(2026, 11, 30)),    # short month: last day
    (31, date(2026, 12, 5), date(2026, 12, 31)),
    (29, date(2027, 2, 1), date(2027, 2, 28)),
])
def test_next_occurrence_clamps_to_month_end(day, as_of, expected):
    assert finance.next_occurrence(day, as_of) == expected


def test_dashboard_survives_a_salary_paid_on_the_31st(con):
    bank = con.execute("SELECT id FROM documents WHERE name = 'Bank_Apr-Oct.csv'").fetchone()[0]
    con.execute("INSERT INTO transactions (id, document_id, row_no, date, description, amount, category, flow, confidence, needs_review) VALUES (999999, ?, 9999, DATE '2026-10-31', 'Salary', 6850, 'Income', 'income', 1, false)", [bank])
    up = finance.dashboard(con, date(2026, 11, 5))["upcoming"]
    assert [u["date"] for u in up if u["label"] == "Salary credit"] == [date(2026, 11, 30)]


def test_emergency_fund_counts_transfers_to_savings(con):
    ef = finance.emergency_fund(con, 6, AS_OF)
    sep = summarize(finance.load_txns(con), date(2026, 9, 1), date(2026, 9, 30))
    need = sep.spending + sep.debt_service
    assert ef["saved"] == 3000
    assert ef["months_covered"] == round(3000 / need, 2)
    assert ef["gap"] == round(6 * need - 3000, 2)


def test_payoff_view_follows_the_saved_strategy_and_extra_payment(con):
    default = finance.payoff_view(con, AS_OF)
    assert default["strategy"] == "avalanche" and default["extra_monthly"] == 500
    assert default["accounts"][0]["name"].startswith("Visa")
    assert default["interest_saved"] > 0 and default["feasible"]
    finance.set_setting(con, "payoff_strategy", "snowball")
    finance.set_setting(con, "extra_monthly", "1200")
    snow = finance.payoff_view(con, AS_OF)
    assert snow["accounts"][0]["name"].startswith("BNPL")
    assert snow["extra_monthly"] == 1200 and snow["debt_free"] < default["debt_free"]


def test_sources_cite_documents_with_their_row_ranges(con):
    src = citations.sources_for(con, ["v_debts"])
    assert {"document": "Loan_Schedule.xlsx", "rows": "rows 1–3", "count": 3} in src


def test_first_of_next_month_rolls_over_the_year():
    assert finance.first_of_next_month(date(2026, 10, 10)) == date(2026, 11, 1)
    assert finance.first_of_next_month(date(2026, 12, 31)) == date(2027, 1, 1)


def test_interest_saved_on_the_dashboard_uses_the_same_minimums_only_baseline_as_the_comparison(con):
    one = finance.payoff_result(con, 500, "avalanche", None, AS_OF)["interest_saved"]
    both = finance.payoff_comparison(con, 500, AS_OF)["interest_saved_vs_minimums"]["avalanche"]
    assert one == pytest.approx(both, abs=0.01)


def test_a_30_day_window_holds_exactly_30_days_counting_both_end_days(con):
    doc = con.execute("SELECT id FROM documents WHERE status = 'ready' LIMIT 1").fetchone()[0]
    con.execute("DELETE FROM transactions")
    for n, (day, amount) in enumerate(((date(2026, 9, 10), -111), (date(2026, 9, 11), -22), (AS_OF, -3)), start=1):
        con.execute("INSERT INTO transactions (id, document_id, row_no, date, amount, category, flow) "
                    "VALUES (?, ?, ?, ?, ?, 'Dining out', 'spending')", [n, doc, n, day, amount])
    assert finance.spend_by_category(con, 30, AS_OF) == {"Dining out": 25}          # Sep 10 is day 31 and stays out
    assert finance.dashboard(con, AS_OF)["spending_30d"]["value"] == 25


def test_dashboard_lists_budget_caps_with_last_30_days_spending(con):
    assert finance.dashboard(con, AS_OF)["caps"] == []
    spent = finance.spend_by_category(con, 30, AS_OF)
    cat = max(spent, key=spent.get)
    con.execute("INSERT INTO budget_caps VALUES (?, ?)", [cat, spent[cat] - 10])
    con.execute("INSERT INTO budget_caps VALUES ('Groceries', 100000)")
    caps = {c["category"]: c for c in finance.dashboard(con, AS_OF)["caps"]}
    assert caps[cat] == {"category": cat, "monthly_cap": spent[cat] - 10, "last_30_days": spent[cat], "over": True}
    assert caps["Groceries"]["over"] is False


def test_income_matches_payslip_when_net_equals_the_bank_salary_credit(con):
    inc = finance.dashboard(con, AS_OF)["income"]
    assert inc["payslip_net"] == 6850 and inc["matches_bank"] is True


def test_income_differs_from_payslip_beyond_one_percent(con):
    con.execute("UPDATE payslips SET net = 6700")
    inc = finance.dashboard(con, AS_OF)["income"]
    assert inc["payslip_net"] == 6700 and inc["matches_bank"] is False
    con.execute("UPDATE payslips SET net = 6800")  # 0.7% off: within tolerance
    assert finance.dashboard(con, AS_OF)["income"]["matches_bank"] is True


def test_income_has_no_payslip_comparison_without_a_payslip(con):
    con.execute("DELETE FROM payslips")
    inc = finance.dashboard(con, AS_OF)["income"]
    assert inc["payslip_net"] is None and inc["matches_bank"] is None


def test_is_duplicate_insight_only_for_in_plan_or_dismissed_with_same_agent_and_title():
    rows = [{"agent": "Budget Coach", "title": "Cap Dining at $300", "status": "in_plan"},
            {"agent": "Debt Analyzer", "title": "Card costs $244", "status": "dismissed"},
            {"agent": "Savings Strategist", "title": "Fund at 1.9 months", "status": "new"}]
    assert finance.is_duplicate_insight(rows, "Budget Coach", "Cap Dining at $300")
    assert finance.is_duplicate_insight(rows, "Debt Analyzer", " card costs $244 ")  # case and spacing do not matter
    assert not finance.is_duplicate_insight(rows, "Budget Coach", "Card costs $244")  # other agent
    assert not finance.is_duplicate_insight(rows, "Savings Strategist", "Fund at 1.9 months")  # still new: it is replaced
    assert not finance.is_duplicate_insight([], "Budget Coach", "x")
