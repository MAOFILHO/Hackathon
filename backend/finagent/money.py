"""Money flows: Transfer detection, Spending and Savings Rate. See CONTEXT.md."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

Flow = Literal["income", "spending", "transfer", "debt_service"]

_TRANSFER = re.compile(
    r"credit card payment|card payment|payment\s*-?\s*thank you|autopay payment|transfer to|transfer from",
    re.I,
)
_DEBT_SERVICE = re.compile(
    r"loan installment|loan emi|bnpl installment|auto loan|car loan|interest charge|finance charge|late fee",
    re.I,
)


@dataclass(frozen=True)
class Txn:
    date: date
    amount: float  # negative = money out
    category: str
    flow: Flow


@dataclass(frozen=True)
class Summary:
    income: float
    spending: float
    debt_service: float
    transfers: float
    savings_rate: float | None


def classify_flow(category: str, description: str) -> Flow:
    """Decide which money flow a Row belongs to. Keywords beat the model's category."""
    if category == "Transfer" or _TRANSFER.search(description):
        return "transfer"
    if category == "Debt Service" or _DEBT_SERVICE.search(description):
        return "debt_service"
    if category == "Income":
        return "income"
    return "spending"


def _in(txns: list[Txn], start: date, end: date) -> list[Txn]:
    return [t for t in txns if start <= t.date <= end]


def summarize(txns: list[Txn], start: date, end: date) -> Summary:
    window = _in(txns, start, end)

    def total(flow: Flow) -> float:
        return round(sum(t.amount for t in window if t.flow == flow), 2)

    income = total("income")
    spending = -total("spending")
    debt_service = -total("debt_service")
    transfers = round(sum(abs(t.amount) for t in window if t.flow == "transfer"), 2)
    rate = (income - spending - debt_service) / income if income > 0 else None
    return Summary(income, spending, debt_service, transfers, rate)


def spending_by_category(txns: list[Txn], start: date, end: date) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in _in(txns, start, end):
        if t.flow == "spending":
            out[t.category] = round(out.get(t.category, 0) - t.amount, 2)
    return out
