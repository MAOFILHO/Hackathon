import json
from datetime import date

import duckdb
import pytest
from fastapi.testclient import TestClient

from finagent import citations, db, finance, ingest, llm, seed, tools
from finagent.api import app
from finagent.schemas import Transaction


def test_format_rows_compresses_consecutive_numbers():
    assert citations.format_rows([4, 9, 12, 13, 14, 15]) == "rows 4, 9, 12–15"


def test_format_rows_single_row_and_unsorted_duplicates():
    assert citations.format_rows([7]) == "row 7"
    assert citations.format_rows([9, 4, 4, 5]) == "rows 4–5, 9"


def test_sources_from_result_cites_exactly_the_returned_rows():
    rows = [{"document": "Card.pdf", "row_no": 12, "page": 2}, {"document": "Card.pdf", "row_no": 4, "page": 1},
            {"document": "Card.pdf", "row_no": 13, "page": 2}, {"document": "Bank.csv", "row_no": 3, "page": None}]
    got = citations.sources_from_result(rows)
    assert got == [
        {"document": "Card.pdf", "rows": "rows 4, 12–13", "count": 3, "row_nos": [4, 12, 13], "pages": "pp. 1–2", "page_nos": [1, 2]},
        {"document": "Bank.csv", "rows": "row 3", "count": 1, "row_nos": [3], "pages": None, "page_nos": []},
    ]


def test_sources_from_result_needs_document_and_row_no_columns():
    assert citations.sources_from_result([{"category": "Dining out", "total": 10}]) == []
    assert citations.sources_from_result([]) == []


def test_merge_sources_unions_rows_per_document():
    a = citations.sources_from_result([{"document": "Card.pdf", "row_no": 2, "page": 1}])
    b = citations.sources_from_result([{"document": "Card.pdf", "row_no": 3, "page": 2}, {"document": "Bank.csv", "row_no": 1}])
    got = citations.merge_sources(a + b)
    assert [s["document"] for s in got] == ["Card.pdf", "Bank.csv"]
    assert got[0]["rows"] == "rows 2–3" and got[0]["count"] == 2 and got[0]["pages"] == "pp. 1–2"


def test_merge_sources_keeps_an_aggregate_range_over_partial_rows():
    aggregate = {"document": "Card.pdf", "rows": "rows 1–139", "count": 139}
    partial = citations.sources_from_result([{"document": "Card.pdf", "row_no": 5}])
    assert citations.merge_sources(partial + [aggregate]) == [aggregate]


@pytest.fixture()
def con(tmp_path, monkeypatch):
    c = db.connect(tmp_path / "c.duckdb")
    seed.load(c)
    monkeypatch.setattr(db, "_con", c)
    return c


def test_page_is_stored_and_visible_in_the_view(con):
    doc = ingest.create_document(con, "Scan.pdf", "pdf", "spending", "ready")
    ingest.store_transactions(con, doc, [Transaction(row_no=1, date=date(2026, 10, 2), description="Cafe", amount=-4, page=3)])
    assert db.rows(con, "SELECT page FROM v_transactions WHERE document = 'Scan.pdf'") == [{"page": 3}]
    assert "page" in db.AGENT_VIEWS["v_transactions"]


def test_old_database_files_gain_the_page_column(tmp_path):
    path = tmp_path / "old.duckdb"
    old = duckdb.connect(str(path))
    old.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, name VARCHAR, file_type VARCHAR, doc_type VARCHAR, status VARCHAR, row_count INTEGER, created_at TIMESTAMP)")
    old.execute("""CREATE TABLE transactions (id INTEGER, document_id INTEGER, row_no INTEGER, date DATE, description VARCHAR,
                   amount DOUBLE, category VARCHAR, flow VARCHAR, confidence DOUBLE, needs_review BOOLEAN)""")
    old.execute("INSERT INTO documents VALUES (1, 'Old.pdf', 'pdf', 'spending', 'ready', 1, now())")
    old.execute("INSERT INTO transactions VALUES (1, 1, 1, DATE '2026-09-01', 'Shop', -10, 'Shopping', 'spending', 0.9, false)")
    old.close()
    con = db.connect(path)
    assert db.rows(con, "SELECT description, page FROM v_transactions") == [{"description": "Shop", "page": None}]


def test_run_sql_cites_exactly_the_rows_it_returned(con):
    doc = ingest.create_document(con, "Scan.pdf", "pdf", "spending", "ready")
    ingest.store_transactions(con, doc, [
        Transaction(row_no=n, date=date(2026, 10, n), description=f"Shop {n}", amount=-n, page=1 + n // 3) for n in range(1, 8)])
    out = json.loads(tools.run_sql.invoke({"query": "SELECT document, row_no, page FROM v_transactions WHERE document = 'Scan.pdf' AND row_no IN (2, 3, 6)"}))
    assert out["sources"] == [{"document": "Scan.pdf", "rows": "rows 2–3, 6", "count": 3, "row_nos": [2, 3, 6], "pages": "pp. 1–3", "page_nos": [1, 2, 3]}]


def test_run_sql_aggregates_still_cite_the_whole_view(con):
    out = json.loads(tools.run_sql.invoke({"query": "SELECT category, sum(amount) AS total FROM v_transactions GROUP BY 1"}))
    assert {s["document"] for s in out["sources"]} == {"Bank_Apr-Oct.csv", "Card_Activity_Apr-Oct.pdf"}
    assert all("–" in s["rows"] for s in out["sources"])


def test_payoff_compare_endpoint_returns_both_strategies(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(db, "_con", db.connect(tmp_path / "cmp.duckdb"))
    with TestClient(app) as c:
        got = c.get("/api/payoff/compare", params={"extra": 1000}).json()
        expected = finance.payoff_comparison(db.get(), 1000)
        for name in ("avalanche", "snowball"):
            assert got[name]["total_interest"] == expected[name]["total_interest"]
            assert got[name]["months"] == expected[name]["months"]
            assert got[name]["debt_free"] == expected[name]["debt_free"].isoformat()
        assert c.get("/api/payoff/compare", params={"extra": -5}).status_code == 422


def test_synthesize_numbers_one_source_per_document_and_tells_the_model(monkeypatch):
    from langchain_core.runnables import RunnableLambda

    from finagent import agents

    seen, events = [], []
    monkeypatch.setattr(agents, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(llm, "sonnet", lambda effort="medium": None)

    def fake(inputs):
        seen.append(inputs.to_string())
        return agents.Answer(markdown="Interest is $244 [1].")

    monkeypatch.setattr(agents, "structured", lambda model, parser: RunnableLambda(fake))
    card = lambda n: citations.sources_from_result([{"document": "Card.pdf", "row_no": n, "page": 1}])  # noqa: E731
    state = {"question": "q", "results": [
        {"agent": "A", "findings": "f1", "calls": [], "sources": card(2) + card(3)},
        {"agent": "B", "findings": "f2", "calls": [], "sources": card(9) + citations.sources_from_result([{"document": "Bank.csv", "row_no": 1}])}]}
    agents.synthesize(state)
    sources = events[-1]["sources"]
    assert [(s["document"], s["rows"]) for s in sources] == [("Card.pdf", "rows 2–3, 9"), ("Bank.csv", "row 1")]
    assert "[1] Card.pdf: rows 2–3, 9 (p. 1)" in seen[0] and "[2] Bank.csv: row 1" in seen[0]
    assert "[n]" in agents.ANSWER_PROMPT.messages[0].prompt.template
