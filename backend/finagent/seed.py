"""Synthetic Documents for the demo user Ravi (USD). Run: uv run python -m finagent.seed

Writes 5 real files to data/samples/ and loads their Rows directly (no model calls),
so the dashboard works offline. Totals follow CONTEXT.md money rules.
"""

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from fpdf import FPDF
from PIL import Image, ImageDraw

from . import db, ingest
from .schemas import DebtAccount, Payslip, Transaction

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"
START, END = date(2026, 4, 1), date(2026, 10, 9)
CARD = {"name": "Visa Card ••••4417", "kind": "credit card", "balance": 9800.0, "apr": 0.299,
        "min_payment": 295.0, "due_day": 14}
LOANS = [
    ("Personal loan - First Bank", "personal loan", 14200, 13.5, 480, 5),
    ("Car loan - AutoFin", "car loan", 12900, 6.5, 395, 20),
    ("BNPL - ShopLater", "bnpl", 600, 0.0, 150, 1),
]
PAYSLIP = {"period": "September 2026", "employer": "Acme Logistics", "gross": 8900.0, "net": 6850.0,
           "lines": [("Federal & state tax", 1420.0), ("Retirement 401(k)", 445.0), ("Health insurance", 185.0)]}
UNSURE = {"POS 88213 MALL", "SQ *CORNER", "AMZN MKTP US", "PAYPAL *JMSTORE"}


def _months():
    d = START
    while d <= END:
        yield d
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)


def _day(m: date, day: int) -> date | None:
    try:
        d = m.replace(day=day)
    except ValueError:
        return None
    return d if START <= d <= END else None


def _adder(rows: list):
    """An `add(month, day, description, amount, category)` that appends a Row when the day exists in that month and window."""
    def add(m, day, desc, amt, cat):
        d = _day(m, day)
        if d:
            rows.append((d, desc, round(amt, 2), cat))

    return add


def bank_rows(rng: random.Random) -> list[tuple[date, str, float, str]]:
    """(date, narration, signed amount, category)."""
    rows = []

    add = _adder(rows)

    for m in _months():
        add(m, 28, "Salary credit - Acme Logistics", 6850, "Income")
        add(m, 1, "Rent - Parkview Apartments", -1900, "Housing")
        add(m, 1, "BNPL installment - ShopLater", -150, "Debt Service")
        add(m, 5, "Personal loan installment - First Bank", -480, "Debt Service")
        add(m, 20, "Car loan installment - AutoFin", -395, "Debt Service")
        add(m, 3, "City Electric utility", -rng.uniform(95, 135), "Utilities")
        add(m, 8, "FiberNet internet", -70, "Utilities")
        add(m, 12, "Mobile phone plan", -45, "Utilities")
        add(m, 2, "Gym membership - FitHub", -49, "Subscriptions")
        add(m, 5, "Streaming subscription", -15.99, "Subscriptions")
        add(m, 6, "Music streaming", -10.99, "Subscriptions")
        add(m, 14, "CREDIT CARD PAYMENT - VISA 4111 2222 3333 4417", -1200, "Transfer")
        add(m, 29, "Transfer to savings", -500, "Transfer")
        for day in (4, 11, 18, 25):
            add(m, day, "FreshMart supermarket", -rng.uniform(130, 185), "Groceries")
        for day in (2, 7, 10, 16, 22, 27):
            add(m, day, "Ride-hailing app", -rng.uniform(11, 32), "Ride-hailing")
        for day in (6, 13, 21, 26):
            add(m, day, "Restaurant - Olive Grill", -rng.uniform(25, 62), "Dining out")
        for day in (3, 9, 15, 19, 24, 30):
            add(m, day, "Corner Coffee cafe", -rng.uniform(4.5, 8), "Dining out")
        add(m, 17, "CityCare pharmacy", -rng.uniform(18, 42), "Health")
        add(m, 23, "POS 88213 MALL", -rng.uniform(60, 140), "Shopping")
    return sorted(rows)


def card_rows(rng: random.Random) -> list[tuple[date, str, float, str]]:
    rows = []

    add = _adder(rows)

    for m in _months():
        for day in (5, 9, 12, 16, 19, 23, 26, 29):
            add(m, day, rng.choice(["Sushi Bar", "Pizza Corner", "Bistro 21", "Burger Lab"]), -rng.uniform(32, 88), "Dining out")
        for day in (8, 21):
            add(m, day, "AMZN MKTP US", -rng.uniform(25, 120), "Shopping")
        add(m, 13, "PAYPAL *JMSTORE", -rng.uniform(40, 150), "Shopping")
        for day in (4, 11, 17, 25):
            add(m, day, "Ride-hailing app", -rng.uniform(14, 36), "Ride-hailing")
        add(m, 3, "Daily News digital", -12, "Subscriptions")
        add(m, 7, "FitTrack app", -14.99, "Subscriptions")
        add(m, 10, "LinguaLearn app", -12.99, "Subscriptions")
        add(m, 18, "PhotoCloud storage", -2.99, "Subscriptions")
        add(m, 22, "SQ *CORNER", -rng.uniform(8, 25), "Dining out")
        add(m, 15, "PAYMENT - THANK YOU", 1200, "Transfer")
        add(m, 28, "INTEREST CHARGE ON PURCHASES", -round(CARD["balance"] * CARD["apr"] / 12, 2), "Debt Service")
    add(date(2026, 10, 1), 4, "ELECTROMART #221", -129.99, "Shopping")
    return sorted(rows)


SAMPLE_FILES = ("Bank_Apr-Oct.csv", "Card_Activity_Apr-Oct.pdf", "Loan_Schedule.xlsx", "Payslip_Sep.pdf", "Receipt_scan.jpg")


def write_files(bank, card) -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    if all((SAMPLES / name).exists() for name in SAMPLE_FILES):
        return  # already written; rewriting would race with a reader of the same file
    pd.DataFrame([{"Txn Date": d.isoformat(), "Narration": desc,
                   "Debit": -a if a < 0 else "", "Credit": a if a > 0 else ""} for d, desc, a, _ in bank]
                 ).to_csv(SAMPLES / "Bank_Apr-Oct.csv", index=False)

    pd.DataFrame([{"Account": n, "Type": k, "Outstanding Balance": b, "APR %": r,
                   "Monthly Installment": p, "Due Day": dd} for n, k, b, r, p, dd in LOANS]
                 ).to_excel(SAMPLES / "Loan_Schedule.xlsx", index=False)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "Visa Card - Account Activity  (card 4111 2222 3333 4417)", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=9)
    for line in (f"Statement period: {START:%b %d, %Y} - {END:%b %d, %Y}", f"Closing balance: ${CARD['balance']:,.2f}",
                 f"Purchase APR: {CARD['apr'] * 100:.1f}%", f"Minimum payment due: ${CARD['min_payment']:,.2f}",
                 "Payment due date: October 14, 2026"):
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    with pdf.table(col_widths=(25, 110, 30), text_align=("LEFT", "LEFT", "RIGHT")) as t:
        t.row().cell("Date"); t.rows[-1].cell("Description"); t.rows[-1].cell("Amount (USD)")
        for d, desc, a, _ in card:
            r = t.row()
            r.cell(d.isoformat()); r.cell(desc); r.cell(f"{a:,.2f}")
    pdf.output(str(SAMPLES / "Card_Activity_Apr-Oct.pdf"))

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, f"{PAYSLIP['employer']} - Payslip {PAYSLIP['period']}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    pdf.cell(0, 6, "Employee: Ravi Kumar   ID: EMP-0442", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    with pdf.table(col_widths=(120, 40), text_align=("LEFT", "RIGHT")) as t:
        for label, v in [("Gross pay", PAYSLIP["gross"]), *[(n, -v) for n, v in PAYSLIP["lines"]], ("Net pay", PAYSLIP["net"])]:
            r = t.row(); r.cell(label); r.cell(f"{v:,.2f}")
    pdf.output(str(SAMPLES / "Payslip_Sep.pdf"))

    img = Image.new("RGB", (420, 520), "white")
    g = ImageDraw.Draw(img)
    for i, line in enumerate(["ELECTROMART #221", "2026-10-04  14:32", "", "Wireless headphones   119.99",
                              "Sales tax               10.00", "", "TOTAL                  129.99",
                              "VISA ****4417  APPROVED"]):
        g.text((30, 30 + i * 34), line, fill="black")
    img.save(SAMPLES / "Receipt_scan.jpg", quality=90)


def _to_txns(rows) -> list[Transaction]:
    return [Transaction(row_no=i, date=d, description=desc, amount=a, category=c,
                        confidence=0.55 if desc in UNSURE else 0.95)
            for i, (d, desc, a, c) in enumerate(rows, start=1)]


def load(con) -> None:
    con.execute("""DELETE FROM transactions; DELETE FROM debt_accounts; DELETE FROM payslips;
                   DELETE FROM documents; DELETE FROM row_fixes; DELETE FROM insights; DELETE FROM budget_caps; DELETE FROM settings;""")
    rng = random.Random(42)
    bank, card = bank_rows(rng), card_rows(rng)
    write_files(bank, card)

    doc = ingest.create_document(con, "Bank_Apr-Oct.csv", "csv", "spending")
    ingest.store_transactions(con, doc, _to_txns(bank))
    ingest.finish(con, doc, "spending", "ready")

    doc = ingest.create_document(con, "Card_Activity_Apr-Oct.pdf", "pdf", "spending")
    txns = _to_txns(card)
    ingest.store_transactions(con, doc, txns)
    ingest.store_debts(con, doc, [DebtAccount(**CARD, row_no=len(txns) + 1)])
    ingest.finish(con, doc, "spending", "ready")

    doc = ingest.create_document(con, "Loan_Schedule.xlsx", "xlsx", "debt")
    ingest.store_debts(con, doc, ingest.table_debts(ingest.read_table(SAMPLES / "Loan_Schedule.xlsx")))
    ingest.finish(con, doc, "debt", "ready")

    doc = ingest.create_document(con, "Payslip_Sep.pdf", "pdf", "income")
    ingest.store_payslip(con, doc, Payslip(**{k: v for k, v in PAYSLIP.items() if k != 'lines'}))
    ingest.finish(con, doc, "income", "ready")

    doc = ingest.create_document(con, "Receipt_scan.jpg", "jpg", "receipt")
    ingest.finish(con, doc, "receipt", "ready")

    con.execute("INSERT INTO settings VALUES ('payoff_strategy', 'avalanche'), ('extra_monthly', '500')")


if __name__ == "__main__":
    con = db.get()
    load(con)
    for r in db.rows(con, "SELECT name, doc_type, status, row_count FROM documents ORDER BY id"):
        print(r)
