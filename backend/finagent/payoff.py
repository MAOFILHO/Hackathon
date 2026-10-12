"""Payoff Strategy simulation. Plain arithmetic; agents call this, never compute it."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

Strategy = Literal["avalanche", "snowball", "custom"]
MAX_MONTHS = 600


@dataclass(frozen=True)
class Debt:
    name: str
    balance: float
    apr: float  # 0.299 = 29.9%
    min_payment: float


@dataclass(frozen=True)
class PayoffResult:
    strategy: str
    extra: float
    feasible: bool
    months: int | None
    debt_free: date | None
    total_interest: float
    payoff_dates: dict[str, date]
    order: list[str]


@dataclass(frozen=True)
class Comparison:
    baseline: PayoffResult
    avalanche: PayoffResult
    snowball: PayoffResult
    interest_saved: dict[str, float]
    months_sooner: dict[str, int]


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _order(debts: list[Debt], strategy: Strategy, order: list[str] | None) -> list[str]:
    avalanche = [d.name for d in sorted(debts, key=lambda d: (-d.apr, d.balance))]
    if strategy == "snowball":
        return [d.name for d in sorted(debts, key=lambda d: (d.balance, -d.apr))]
    if strategy == "custom" and order:
        return [n for n in order if n in avalanche] + [n for n in avalanche if n not in order]
    return avalanche


def simulate(debts: list[Debt], extra: float, strategy: Strategy, start: date,
             order: list[str] | None = None, rollover: bool = True) -> PayoffResult:
    """rollover=False pays every debt only its own minimum, so a paid-off debt frees nothing for the others."""
    seq = _order(debts, strategy, order)
    bal = {d.name: d.balance for d in debts}
    by_name = {d.name: d for d in debts}
    budget = sum(d.min_payment for d in debts) + extra  # freed minimums roll over
    paid: dict[str, date] = {}
    interest = 0.0

    for month in range(1, MAX_MONTHS + 1):
        when = add_months(start, month - 1)
        for name in seq:
            if bal[name] > 0:
                i = bal[name] * by_name[name].apr / 12
                bal[name] += i
                interest += i
        left = budget
        for name in seq:
            pay = min(by_name[name].min_payment, bal[name], left)
            bal[name] -= pay
            left -= pay
        if rollover:
            for name in seq:
                pay = min(bal[name], left)
                bal[name] -= pay
                left -= pay
        for name in seq:
            if name not in paid and bal[name] <= 0.005:
                bal[name] = 0
                paid[name] = when
        if len(paid) == len(debts):
            return PayoffResult(strategy, extra, True, month, when, round(interest, 2), paid, seq)

    return PayoffResult(strategy, extra, False, None, None, round(interest, 2), paid, seq)


def minimums_only(debts: list[Debt], start: date) -> PayoffResult:
    """The baseline every saving is measured against: each debt gets its own minimum and nothing else."""
    return simulate(debts, 0, "avalanche", start, rollover=False)


def compare(debts: list[Debt], extra: float, start: date) -> Comparison:
    base = minimums_only(debts, start)
    av = simulate(debts, extra, "avalanche", start)
    sn = simulate(debts, extra, "snowball", start)
    saved = {r.strategy: round(base.total_interest - r.total_interest, 2) for r in (av, sn)}
    sooner = {r.strategy: (base.months or MAX_MONTHS) - (r.months or MAX_MONTHS) for r in (av, sn)}
    return Comparison(base, av, sn, saved, sooner)
