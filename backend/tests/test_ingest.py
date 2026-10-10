import pandas as pd
import pytest

from finagent import db, ingest, llm


@pytest.fixture()
def con(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)  # keyword categorizer, no network
    return db.connect(tmp_path / "t.duckdb")


def test_debit_credit_csv_becomes_signed_categorized_rows(con, tmp_path):
    path = tmp_path / "bank.csv"
    pd.DataFrame([
        {"Txn Date": "2026-09-01", "Narration": "Salary credit - Acme", "Debit": None, "Credit": "6,850.00"},
        {"Txn Date": "09/14/2026", "Narration": "CREDIT CARD PAYMENT - VISA 4111 2222 3333 4417", "Debit": "1,200.00", "Credit": None},
        {"Txn Date": "2026-09-04", "Narration": "FreshMart supermarket", "Debit": "142.10", "Credit": None},
    ]).to_csv(path, index=False)
    doc = ingest.create_document(con, "bank.csv", "csv", "spending")

    mapping = ingest.ingest(con, doc, path)

    assert (mapping.date, mapping.description, mapping.debit, mapping.credit) == ("Txn Date", "Narration", "Debit", "Credit")
    rows = db.rows(con, "SELECT row_no, date::VARCHAR d, description, amount, category, flow FROM transactions ORDER BY row_no")
    assert rows == [
        {"row_no": 1, "d": "2026-09-01", "description": "Salary credit - Acme", "amount": 6850.0, "category": "Income", "flow": "income"},
        {"row_no": 2, "d": "2026-09-14", "description": "CREDIT CARD PAYMENT - VISA ••••4417", "amount": -1200.0, "category": "Transfer", "flow": "transfer"},
        {"row_no": 3, "d": "2026-09-04", "description": "FreshMart supermarket", "amount": -142.1, "category": "Groceries", "flow": "spending"},
    ]
    assert con.execute("SELECT status FROM documents").fetchone()[0] == "review"


def test_loan_schedule_xlsx_becomes_debt_accounts_with_decimal_apr(con, tmp_path):
    path = tmp_path / "loans.xlsx"
    pd.DataFrame([{"Account": "Car loan", "Type": "car loan", "Outstanding Balance": 12900,
                   "APR %": 6.5, "Monthly Installment": 395, "Due Day": 20}]).to_excel(path, index=False)
    doc = ingest.create_document(con, "loans.xlsx", "xlsx", "debt")

    ingest.ingest(con, doc, path)

    assert db.rows(con, "SELECT name, balance, apr, min_payment, due_day FROM debt_accounts") == [
        {"name": "Car loan", "balance": 12900.0, "apr": 0.065, "min_payment": 395.0, "due_day": 20}]


# --- Haiku failures must never hide uncertainty (Needs Review) -------------------------

from datetime import date

from langchain_core.runnables import RunnableLambda

from finagent.schemas import Categorized, CategorizedBatch, RawTransaction


def _raws():
    return [RawTransaction(row_no=1, date=date(2026, 9, 1), description="FreshMart supermarket", amount=-40),
            RawTransaction(row_no=2, date=date(2026, 9, 2), description="Restaurant Olive Grill", amount=-30)]


def test_model_failure_flags_every_row_for_review(monkeypatch):
    def boom(_):
        raise RuntimeError("model down")

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "haiku", lambda: None)
    monkeypatch.setattr(ingest, "structured", lambda model, parser: RunnableLambda(boom))
    rows = ingest.categorize(_raws())
    assert [r.needs_review for r in rows] == [True, True]
    assert [r.category for r in rows] == ["Groceries", "Dining out"]  # best guess is kept


def test_rows_the_model_skipped_are_flagged_for_review(monkeypatch):
    only_first = CategorizedBatch(rows=[Categorized(row_no=1, category="Groceries", confidence=0.95)])
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "haiku", lambda: None)
    monkeypatch.setattr(ingest, "structured", lambda model, parser: RunnableLambda(lambda _: only_first))
    rows = ingest.categorize(_raws())
    assert [r.needs_review for r in rows] == [False, True]


# --- Mapping is saved per Document and limited to table doc types ----------------------

import json

from pydantic import ValidationError


@pytest.mark.parametrize("doc_type", ["income", "receipt"])
def test_mapping_rejects_doc_types_a_table_cannot_be(doc_type):
    with pytest.raises(ValidationError):
        ingest.Mapping(doc_type=doc_type)


def _bank_csv(tmp_path):
    path = tmp_path / "bank.csv"
    path.write_text("Txn Date,Narration,Amount\n2026-09-01,Mystery vendor,-20\n2026-09-02,FreshMart supermarket,-40\n")
    return path


def test_ingest_saves_the_mapping_on_the_document(con, tmp_path):
    doc = ingest.create_document(con, "bank.csv", "csv", "spending")
    ingest.ingest(con, doc, _bank_csv(tmp_path))
    saved = json.loads(con.execute("SELECT mapping FROM documents WHERE id = ?", [doc]).fetchone()[0])
    assert saved["date"] == "Txn Date" and saved["amount"] == "Amount" and saved["doc_type"] == "spending"


def test_ingest_reapplies_the_users_row_fixes_and_clears_a_past_error(con, tmp_path):
    doc = ingest.create_document(con, "bank.csv", "csv", "spending")
    con.execute("UPDATE documents SET error = 'old failure' WHERE id = ?", [doc])
    ingest.ingest(con, doc, _bank_csv(tmp_path))
    ingest.fix_row(con, doc, 1, "Shopping")

    ingest.ingest(con, doc, _bank_csv(tmp_path))

    row = db.rows(con, "SELECT category, confidence, needs_review FROM transactions WHERE row_no = 1")[0]
    assert row == {"category": "Shopping", "confidence": 1.0, "needs_review": False}
    assert con.execute("SELECT error FROM documents WHERE id = ?", [doc]).fetchone()[0] is None


def test_ingest_can_finish_a_document_as_ready(con, tmp_path):
    doc = ingest.create_document(con, "bank.csv", "csv", "spending")
    ingest.ingest(con, doc, _bank_csv(tmp_path), status="ready")
    assert con.execute("SELECT status FROM documents WHERE id = ?", [doc]).fetchone()[0] == "ready"
