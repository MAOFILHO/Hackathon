import json
from datetime import datetime

from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda

from finagent import agents, db, llm
from finagent.agents import InsightOut
from finagent.api import app


def _insert(con, agent, title, status, evidence=None):
    con.execute("INSERT INTO insights VALUES (?, ?, ?, 'b', 'add_to_plan', '{}', '[]', ?, ?, ?)",
                [db.next_id(con), agent, title, status, datetime.now(), evidence])


def test_insights_api_returns_the_evidence_tool_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(db, "_con", db.connect(tmp_path / "ev.duckdb"))
    evidence = [{"tool": "get_debts", "rows": 4}, {"tool": "debt_cost", "rows": None}]
    with TestClient(app) as c:  # starting the app seeds the sample Documents
        _insert(db.get(), "Debt Analyzer", "With evidence", "new", json.dumps(evidence))
        _insert(db.get(), "Budget Coach", "Old row", "new")  # insights saved before the column existed
        got = {i["title"]: i for i in c.get("/api/insights").json()}
    assert got["With evidence"]["evidence"] == evidence
    assert got["Old row"]["evidence"] == []


def test_connect_adds_the_evidence_column_to_an_existing_database(tmp_path):
    import duckdb
    path = tmp_path / "old.duckdb"
    old = duckdb.connect(str(path))
    old.execute("CREATE TABLE insights (id INTEGER, agent VARCHAR, title VARCHAR, body VARCHAR, action_kind VARCHAR, "
                "action_payload VARCHAR, sources VARCHAR, status VARCHAR, created_at TIMESTAMP)")
    old.close()
    con = db.connect(path)
    assert "evidence" in [r[0] for r in con.execute("DESCRIBE insights").fetchall()]


def test_generate_insights_stores_tool_calls_and_skips_duplicates_of_decided_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_con", db.connect(tmp_path / "gen.duckdb"))
    calls = [{"tool": "get_debts", "args": {}, "rows": 4}]
    monkeypatch.setattr(agents, "run_specialist", lambda name, task: {"agent": name, "findings": "f", "calls": calls, "sources": []})
    monkeypatch.setattr(llm, "sonnet", lambda effort="low": None)
    monkeypatch.setattr(agents, "structured", lambda model, parser: RunnableLambda(
        lambda _: InsightOut(title="T", body="b", action_kind="add_to_plan")))
    con = db.get()
    _insert(con, "Debt Analyzer", "T", "dismissed")
    agents.generate_insights()
    rows = db.rows(con, "SELECT agent, status, evidence FROM insights ORDER BY id")
    assert [r["agent"] for r in rows].count("Debt Analyzer") == 1  # duplicate of the dismissed one was not added
    fresh = [r for r in rows if r["agent"] == "Budget Coach"]
    assert json.loads(fresh[0]["evidence"]) == [{"tool": "get_debts", "rows": 4}]
