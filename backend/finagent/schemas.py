"""One shape for every Row, whatever produced it: CSV, XLSX, Claude reading a PDF, or seed data.

Every model output is validated against these models (structured output), so malformed output fails loudly
instead of reaching the database.
"""

import math
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from .money import Flow, classify_flow
from .privacy import mask

CATEGORIES = [
    "Income", "Housing", "Utilities", "Groceries", "Dining out", "Transport", "Ride-hailing",
    "Shopping", "Subscriptions", "Health", "Travel", "Entertainment", "Transfer", "Debt Service", "Other",
]
REVIEW_BELOW = 0.75
DocType = Literal["spending", "debt", "income", "receipt"]
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%Y/%m/%d")


def _cents(n) -> float:
    return float(Decimal(str(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def parse_amount(v) -> float:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 0.0
    if isinstance(v, (int, float)):
        return _cents(v)
    s = str(v).strip()
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d.\-]", "", s)
    if not s or s in ("-", "."):
        return 0.0
    n = Decimal(s)
    return _cents(-abs(n) if negative else n)


def parse_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date: {v!r}")


class RawTransaction(BaseModel):
    """A transaction as printed in a Document, before categorization."""

    row_no: int = Field(ge=1, description="1-based position of the row in the document")
    date: date
    description: str
    amount: float = Field(description="Negative for money out (purchases, payments made), positive for money in")
    page: int | None = Field(default=None, description="PDF page the row was printed on")

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, v):
        return parse_date(v)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, v):
        return parse_amount(v)

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, v):
        return mask(" ".join(str(v or "").split()))


class Transaction(RawTransaction):
    """The canonical Row stored in the database and returned by the API."""

    category: str = "Other"
    confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v):
        return v if v in CATEGORIES else "Other"

    @model_validator(mode="after")
    def _category_matches_flow(self):
        detected = classify_flow(self.category, self.description)
        if detected == "transfer":
            self.category = "Transfer"
        elif detected == "debt_service":
            self.category = "Debt Service"
        return self

    @computed_field
    @property
    def flow(self) -> Flow:
        return classify_flow(self.category, self.description)

    @computed_field
    @property
    def needs_review(self) -> bool:
        return self.confidence < REVIEW_BELOW


class DebtAccount(BaseModel):
    row_no: int = Field(default=1, ge=1)
    name: str
    kind: str
    balance: float
    apr: float = Field(ge=0, le=1, description="Decimal, 0.299 for 29.9%")
    min_payment: float
    due_day: int | None = Field(default=None, ge=1, le=31)

    @field_validator("apr", mode="before")
    @classmethod
    def _apr(cls, v):
        n = parse_amount(v) if not isinstance(v, float) else v
        return round(n / 100, 6) if n > 1 else n

    @field_validator("balance", "min_payment", mode="before")
    @classmethod
    def _money(cls, v):
        return abs(parse_amount(v))

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        return mask(str(v).strip())


class Payslip(BaseModel):
    period: str
    employer: str
    gross: float
    net: float

    @field_validator("period", "employer", mode="before")
    @classmethod
    def _text(cls, v):
        return mask(" ".join(str(v or "").split()))

    @field_validator("gross", "net", mode="before")
    @classmethod
    def _money(cls, v):
        return abs(parse_amount(v))


class Extraction(BaseModel):
    """What Claude returns after reading a PDF or image."""

    doc_type: DocType
    transactions: list[RawTransaction] = Field(default_factory=list)
    debts: list[DebtAccount] = Field(default_factory=list, description="Card or loan accounts with balance and APR")
    payslip: Payslip | None = None


class Categorized(BaseModel):
    row_no: int
    category: str = Field(description="One of the allowed categories")
    confidence: float = Field(ge=0, le=1)

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v):
        return v if v in CATEGORIES else "Other"


class CategorizedBatch(BaseModel):
    rows: list[Categorized]


extraction_parser = PydanticOutputParser(pydantic_object=Extraction)
categorization_parser = PydanticOutputParser(pydantic_object=CategorizedBatch)
