from datetime import date

import pytest

from finagent.payoff import Debt, compare, simulate

START = date(2026, 10, 1)


def test_zero_apr_debt_pays_off_in_balance_over_payment_months():
    r = simulate([Debt("BNPL", 600, 0.0, 150)], extra=0, strategy="avalanche", start=START)
    assert r.feasible
    assert r.months == 4
    assert r.total_interest == 0
    assert r.payoff_dates["BNPL"] == date(2027, 1, 1)
    assert r.debt_free == date(2027, 1, 1)


def test_interest_accrues_monthly_at_apr_over_12():
    # 1200 at 12% APR, 1100/month: month 1 interest = 12, month 2 pays the rest.
    r = simulate([Debt("Card", 1200, 0.12, 1100)], extra=0, strategy="avalanche", start=START)
    assert r.months == 2
    assert r.total_interest == pytest.approx(12 + (1200 + 12 - 1100) * 0.01, abs=0.01)


def test_avalanche_targets_highest_apr_first():
    debts = [Debt("Low", 1000, 0.05, 50), Debt("High", 1000, 0.30, 50)]
    r = simulate(debts, extra=500, strategy="avalanche", start=START)
    assert r.payoff_dates["High"] < r.payoff_dates["Low"]


def test_snowball_targets_smallest_balance_first():
    debts = [Debt("Small", 500, 0.05, 50), Debt("Big", 5000, 0.30, 100)]
    r = simulate(debts, extra=500, strategy="snowball", start=START)
    assert r.payoff_dates["Small"] < r.payoff_dates["Big"]


def test_custom_order_is_respected():
    debts = [Debt("A", 1000, 0.30, 50), Debt("B", 1000, 0.05, 50)]
    r = simulate(debts, extra=500, strategy="custom", order=["B", "A"], start=START)
    assert r.payoff_dates["B"] < r.payoff_dates["A"]


def test_freed_minimums_roll_over_to_next_target():
    debts = [Debt("A", 300, 0.0, 100), Debt("B", 3000, 0.0, 100)]
    with_rollover = simulate(debts, extra=200, strategy="snowball", start=START)
    # A gone after month 1 (100 min + 200 extra). Then B gets 400/month:
    # B paid 100 in month 1, 2900 left / 400 = 7.25 -> 8 more months.
    assert with_rollover.months == 9


def test_avalanche_never_costs_more_interest_than_snowball():
    debts = [Debt("Card", 9800, 0.299, 295), Debt("Personal", 14200, 0.135, 480),
             Debt("Car", 12900, 0.065, 395), Debt("BNPL", 600, 0.0, 150)]
    av = simulate(debts, extra=1000, strategy="avalanche", start=START)
    sn = simulate(debts, extra=1000, strategy="snowball", start=START)
    assert av.total_interest <= sn.total_interest


def test_minimum_below_interest_is_infeasible():
    r = simulate([Debt("Card", 10000, 0.30, 100)], extra=0, strategy="avalanche", start=START)
    assert not r.feasible
    assert r.debt_free is None


def test_the_minimums_only_baseline_pays_each_debt_its_own_minimum_and_rolls_nothing_over():
    debts = [Debt("Card", 5000, 0.25, 150), Debt("Car", 8000, 0.065, 250)]
    alone = [simulate([d], extra=0, strategy="avalanche", start=START) for d in debts]
    base = compare(debts, extra=500, start=START).baseline
    assert base.total_interest == pytest.approx(sum(r.total_interest for r in alone), abs=0.01)
    assert base.months == max(r.months for r in alone)


def test_compare_reports_months_sooner_and_interest_saved_vs_minimums():
    debts = [Debt("Card", 5000, 0.25, 150), Debt("Car", 8000, 0.065, 250)]
    c = compare(debts, extra=500, start=START)
    assert c.baseline.months > c.avalanche.months
    assert c.interest_saved["avalanche"] == pytest.approx(
        c.baseline.total_interest - c.avalanche.total_interest, abs=0.01)
    assert c.months_sooner["avalanche"] == c.baseline.months - c.avalanche.months
