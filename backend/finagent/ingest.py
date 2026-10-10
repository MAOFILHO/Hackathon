"""Turn Documents into Rows: parse, categorize, store. See CONTEXT.md (Document, Row, Needs Review).

Every path produces schemas.Transaction / DebtAccount / Payslip, so all Rows share one format.
"""

import base64
import re
from datetime import datetime
from typing import Literal
from pathlib import Path

import pandas as pd
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from . import db, llm
from .schemas import (CATEGORIES, DebtAccount, Extraction, Payslip, RawTransaction, Transaction,
                      categorization_parser, extraction_parser, parse_amount)

# Offline fallback when no model is reachable; the model handles everything else.
_KEYWORDS = {
    "Income": r"salary|payroll",
    "Housing": r"rent|apartment",
    "Utilities": r"electric|internet|mobile|water|utility",
    "Groceries": r"supermarket|grocer|freshmart|market",
    "Dining out": r"restaurant|cafe|coffee|pizza|grill|sushi|burger|bistro",
    "Ride-hailing": r"ride|uber|lyft",
    "Subscriptions": r"streaming|music|gym|subscription|cloud|news|app",
    "Health": r"pharmacy|clinic",
    "Shopping": r"store|mart|shop|mall",
}


structured = llm.structured  # alias: tests monkeypatch ingest.structured


# --- Column mapping for tables ---------------------------------------------

class Mapping(BaseModel):
    date: str | None = None
    description: str | None = None
    amount: str | None = None  # single signed column
    debit: str | None = None   # or a debit/credit pair
    credit: str | None = None
    doc_type: Literal["spending", "debt"] = "spending"  # income and receipts only come from PDFs


def _find(cols: list[str], *patterns: str) -> str | None:
    for p in patterns:
        for c in cols:
            if re.search(p, c, re.I):
                return c
    return None


def guess_mapping(df: pd.DataFrame) -> Mapping:
    cols = [str(c) for c in df.columns]
    if _find(cols, r"apr|interest rate"):
        return Mapping(doc_type="debt")
    debit, credit = _find(cols, r"debit|withdrawal"), _find(cols, r"credit|deposit")
    return Mapping(
        date=_find(cols, r"date"),
        description=_find(cols, r"narration|description|details|merchant|memo"),
        amount=None if debit or credit else _find(cols, r"amount"),
        debit=debit, credit=credit,
    )


def read_table(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
    return df.astype(object).where(pd.notna(df), None)


def table_transactions(df: pd.DataFrame, m: Mapping) -> list[RawTransaction]:
    out = []
    for i, r in enumerate(df.to_dict("records"), start=1):
        if m.debit or m.credit:
            amount = parse_amount(r.get(m.credit) or 0) - parse_amount(r.get(m.debit) or 0)
        else:
            amount = r.get(m.amount) or 0
        out.append(RawTransaction(row_no=i, date=r.get(m.date), description=r.get(m.description) or "", amount=amount))
    return out


def table_debts(df: pd.DataFrame) -> list[DebtAccount]:
    cols = [str(c) for c in df.columns]
    name, kind = _find(cols, r"name|account|lender"), _find(cols, r"type|kind")
    bal, apr = _find(cols, r"balance|outstanding"), _find(cols, r"apr|interest rate")
    pay, due = _find(cols, r"installment|payment|emi"), _find(cols, r"due")
    return [DebtAccount(row_no=i, name=r.get(name), kind=str(r.get(kind) or "loan"), balance=r.get(bal) or 0,
                        apr=r.get(apr) or 0, min_payment=r.get(pay) or 0,
                        due_day=int(r[due]) if due and r.get(due) else None)
            for i, r in enumerate(df.to_dict("records"), start=1)]


# --- PDF extraction (Claude reads the file directly) ---------------

EXTRACT_PROMPT = """Extract every financial row from this document.
- Bank or card statement: list every transaction (doc_type "spending"), numbering row_no from 1 in printed order.
  If it is a card statement, also return the card under debts with its closing balance, APR, minimum payment and due day.
- Loan schedule: return each loan under debts (doc_type "debt").
- Payslip: fill payslip (doc_type "income").
- Receipt: doc_type "receipt" with one transaction for the total paid.
Copy amounts exactly as printed. Do not invent rows."""


def extract_file(path: Path) -> Extraction:
    block = {"type": "file", "base64": base64.b64encode(path.read_bytes()).decode(), "mime_type": "application/pdf"}
    chain = structured(llm.sonnet(effort="low"), extraction_parser)
    return chain.invoke([{"role": "user", "content": [block, {"type": "text", "text": EXTRACT_PROMPT}]}])


# --- Categorization ------------------------------------------------------------

UNSURE_CONFIDENCE = 0.5  # below schemas.REVIEW_BELOW, so the Row is flagged Needs Review

CATEGORIZE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "You categorize bank and card rows. Allowed categories: {categories}.\n"
               "Card payments and moves between the user's own accounts are Transfer. Loan installments, "
               "interest and fees are Debt Service. Salary and payroll credits are Income. "
               "Use a confidence below 0.75 when the merchant is unclear."),
    ("human", "row_no\tdescription\tamount\n{rows}"),
])


def _keyword_category(t: RawTransaction) -> tuple[str, float]:
    for cat, pattern in _KEYWORDS.items():
        if re.search(pattern, t.description, re.I):
            return cat, 0.8
    return ("Income", 0.5) if t.amount > 0 else ("Other", 0.4)


def categorize(raws: list[RawTransaction]) -> list[Transaction]:
    """Haiku assigns category and confidence when reachable; keyword rules otherwise."""
    guesses = {t.row_no: _keyword_category(t) for t in raws}
    if raws and llm.available():
        chain = CATEGORIZE_PROMPT | structured(llm.haiku(), categorization_parser)
        answered: set[int] = set()
        try:
            for i in range(0, len(raws), 150):
                batch = raws[i:i + 150]
                result = chain.invoke({"categories": ", ".join(CATEGORIES),
                                       "rows": "\n".join(f"{t.row_no}\t{t.description}\t{t.amount}" for t in batch)})
                guesses.update({r.row_no: (r.category, r.confidence) for r in result.rows})
                answered.update(r.row_no for r in result.rows)
        except Exception:
            pass  # keep the keyword guesses, but never present them as certain
        for t in raws:
            if t.row_no not in answered:
                category, confidence = guesses[t.row_no]
                guesses[t.row_no] = (category, min(confidence, UNSURE_CONFIDENCE))
    return [Transaction(**t.model_dump(), category=guesses[t.row_no][0], confidence=guesses[t.row_no][1])
            for t in raws]


# --- Storage -------------------------------------------------------------------

def create_document(con, name: str, file_type: str, doc_type: str, status: str = "extracting") -> int:
    doc_id = db.next_id(con)
    con.execute("INSERT INTO documents (id, name, file_type, doc_type, status, row_count, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)", [doc_id, name, file_type, doc_type, status, datetime.now()])
    return doc_id


def store_transactions(con, doc_id: int, txns: list[Transaction]) -> None:
    for t in txns:
        con.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            db.next_id(con), doc_id, t.row_no, t.date, t.description, t.amount, t.category,
            t.flow, t.confidence, t.needs_review, t.page])


def store_debts(con, doc_id: int, debts: list[DebtAccount]) -> None:
    for d in debts:
        con.execute("INSERT INTO debt_accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            db.next_id(con), doc_id, d.row_no, d.name, d.kind, d.balance, d.apr, d.min_payment, d.due_day])


def store_payslip(con, doc_id: int, p: Payslip) -> None:
    con.execute("INSERT INTO payslips VALUES (?, ?, 1, ?, ?, ?, ?)",
                [db.next_id(con), doc_id, p.period, p.employer, p.gross, p.net])


def finish(con, doc_id: int, doc_type: str, status: str = "review") -> None:
    n = con.execute("""SELECT (SELECT count(*) FROM transactions WHERE document_id = $1)
                            + (SELECT count(*) FROM debt_accounts WHERE document_id = $1)
                            + (SELECT count(*) FROM payslips WHERE document_id = $1)""", [doc_id]).fetchone()[0]
    con.execute("UPDATE documents SET status = ?, row_count = ?, doc_type = ? WHERE id = ?",
                [status, n, doc_type, doc_id])


def fix_row(con, doc_id: int, row_no: int, category: str) -> Transaction | None:
    """Apply the user's category to a Row and remember it, so a re-extract keeps it."""
    found = db.rows(con, "SELECT row_no, date, description, amount FROM transactions WHERE document_id = ? AND row_no = ?",
                    [doc_id, row_no])
    if not found:
        return None
    t = Transaction(**found[0], category=category, confidence=1.0)
    con.execute("UPDATE transactions SET category = ?, flow = ?, confidence = 1.0, needs_review = false "
                "WHERE document_id = ? AND row_no = ?", [t.category, t.flow, doc_id, row_no])
    con.execute("DELETE FROM row_fixes WHERE document_id = ? AND row_no = ?", [doc_id, row_no])
    con.execute("INSERT INTO row_fixes VALUES (?, ?, ?)", [doc_id, row_no, category])
    return t


def _reapply_fixes(con, doc_id: int) -> None:
    for fix in db.rows(con, "SELECT row_no, category FROM row_fixes WHERE document_id = ?", [doc_id]):
        fix_row(con, doc_id, fix["row_no"], fix["category"])


def ingest(con, doc_id: int, path: Path, mapping: Mapping | None = None, status: str = "review") -> Mapping | None:
    """Parse a Document into Rows; it waits in 'review' for the user to confirm unless told otherwise."""
    db.clear_rows(con, doc_id)
    con.execute("UPDATE documents SET error = NULL WHERE id = ?", [doc_id])
    if path.suffix.lower() in (".csv", ".xlsx"):
        df = read_table(path)
        mapping = mapping or guess_mapping(df)
        if mapping.doc_type == "debt":
            store_debts(con, doc_id, table_debts(df))
        else:
            store_transactions(con, doc_id, categorize(table_transactions(df, mapping)))
        con.execute("UPDATE documents SET mapping = ? WHERE id = ?", [mapping.model_dump_json(), doc_id])
        _reapply_fixes(con, doc_id)
        finish(con, doc_id, mapping.doc_type, status)
        return mapping
    ex = extract_file(path)
    if ex.doc_type != "receipt":  # receipts are evidence; the card statement already holds the purchase
        store_transactions(con, doc_id, categorize(ex.transactions))
    base = len(ex.transactions)
    store_debts(con, doc_id, [d.model_copy(update={"row_no": base + i}) for i, d in enumerate(ex.debts, start=1)])
    if ex.payslip:
        store_payslip(con, doc_id, ex.payslip)
    _reapply_fixes(con, doc_id)
    finish(con, doc_id, ex.doc_type, status)
    return None


DOC_COLUMNS = "id, name, file_type, doc_type, status, row_count, created_at, error"  # not the saved mapping


def preview(con, doc_id: int, path: Path | None, limit: int = 50) -> dict:
    doc = db.rows(con, f"SELECT {DOC_COLUMNS} FROM documents WHERE id = ?", [doc_id])[0]
    columns = [str(c) for c in read_table(path).columns] if path and path.suffix.lower() in (".csv", ".xlsx") else []
    txns = [Transaction(**r).model_dump(mode="json") for r in db.rows(con, """
        SELECT row_no, date, description, amount, category, confidence FROM transactions
        WHERE document_id = ? ORDER BY needs_review DESC, row_no LIMIT ?""", [doc_id, limit])]
    debts = [DebtAccount(**r).model_dump() for r in db.rows(con, """
        SELECT row_no, name, kind, balance, apr, min_payment, due_day FROM debt_accounts
        WHERE document_id = ? ORDER BY row_no""", [doc_id])]
    payslips = [Payslip(**r).model_dump() for r in db.rows(con,
        "SELECT period, employer, gross, net FROM payslips WHERE document_id = ?", [doc_id])]
    review = con.execute("SELECT count(*) FROM transactions WHERE document_id = ? AND needs_review", [doc_id]).fetchone()[0]
    return {"document": doc, "columns": columns, "transactions": txns, "debts": debts,
            "payslips": payslips, "needs_review": review}
