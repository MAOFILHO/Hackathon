import json
import logging

from fastapi.testclient import TestClient

from finagent import agents, config, db, llm
from finagent.api import app


def _client(tmp_path, monkeypatch, name="routes"):
    monkeypatch.setattr(db, "_con", db.connect(tmp_path / f"{name}.duckdb"))
    return TestClient(app)


def _events(response):
    return [json.loads(line[len("data:"):]) for line in response.text.splitlines() if line.startswith("data:")]


# --- Budget Caps -------------------------------------------------------------------------------

def test_budget_caps_save_and_replace(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        first = c.post("/api/budget-caps", json=[{"category": "Dining out", "monthly_cap": 300},
                                                 {"category": "Groceries", "monthly_cap": 600}])
        assert first.status_code == 200
        assert {r["category"]: r["monthly_cap"] for r in first.json()} == {"Dining out": 300, "Groceries": 600}
        second = c.post("/api/budget-caps", json=[{"category": "Dining out", "monthly_cap": 250}])
        assert {r["category"]: r["monthly_cap"] for r in second.json()} == {"Dining out": 250, "Groceries": 600}
        saved = db.rows(db.get(), "SELECT * FROM budget_caps WHERE category = 'Dining out'")
        assert len(saved) == 1 and saved[0]["monthly_cap"] == 250


def test_budget_caps_reject_bad_input(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        assert c.post("/api/budget-caps", json=[{"category": "Not a category", "monthly_cap": 100}]).status_code == 422
        assert c.post("/api/budget-caps", json=[{"category": "Groceries", "monthly_cap": "lots"}]).status_code == 422
        assert c.post("/api/budget-caps", json=[{"category": "Groceries"}]).status_code == 422
        assert db.rows(db.get(), "SELECT * FROM budget_caps") == []


# --- Demo reset / agents ------------------------------------------------------------------------

def test_demo_reset_restores_the_sample_documents(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        docs = c.get("/api/documents").json()
        assert len(docs) == 5
        assert c.delete(f"/api/documents/{docs[0]['id']}").status_code == 200
        assert len(c.get("/api/documents").json()) == 4
        assert "reset" in c.post("/api/demo/reset").json()
        names = sorted(d["name"] for d in c.get("/api/documents").json())
        assert names == sorted(d["name"] for d in docs)


def test_agents_endpoint_returns_the_status_of_all_five_agents(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        status = c.get("/api/agents").json()
    assert set(status) == {"Orchestrator", *agents.SPECIALISTS} and len(status) == 5
    assert set(status.values()) <= {"idle", "running", "done"}


# --- Dashboard ----------------------------------------------------------------------------------

def test_dashboard_tells_the_frontend_the_user_name(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        assert c.get("/api/dashboard").json()["user"] == config.USER_NAME


# --- Chat stream --------------------------------------------------------------------------------

class _Graph:
    def __init__(self, chunks=(), error=None):
        self.chunks, self.error = chunks, error

    def stream(self, state, **_):
        yield from self.chunks
        if self.error:
            raise self.error


def test_chat_streams_events_in_order_and_ends(tmp_path, monkeypatch):
    chunks = [{"data": {"type": "agent_start", "agent": "Debt Analyzer"}},
              {"data": {"type": "answer", "text": "first"}},
              {"data": {"type": "answer", "text": "second"}}]
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(agents, "graph", _Graph(chunks))
    monkeypatch.setitem(agents.STATUS, "Orchestrator", "running")
    monkeypatch.setitem(agents.STATUS, "Debt Analyzer", "running")
    with _client(tmp_path, monkeypatch) as c:
        events = _events(c.post("/api/chat", json={"question": "hi"}))
    assert [e["type"] for e in events] == ["agent_start", "answer", "answer", "end"]
    assert [e.get("text") for e in events[1:3]] == ["first", "second"]
    assert agents.STATUS["Orchestrator"] == "idle" and agents.STATUS["Debt Analyzer"] == "idle"


def test_chat_error_event_is_generic_and_the_real_error_is_logged(tmp_path, monkeypatch, caplog):
    secret = "sk-ant-SECRET-1234 /Users/marco/internal/path"
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(agents, "graph", _Graph(error=RuntimeError(secret)))
    with _client(tmp_path, monkeypatch) as c, caplog.at_level(logging.ERROR):
        response = c.post("/api/chat", json={"question": "hi"})
    events = _events(response)
    assert events[-2] == {"type": "error", "message": "The advisor hit a problem. Please try again."}
    assert events[-1] == {"type": "end"}
    assert "SECRET" not in response.text
    assert any(secret in (r.exc_text or "") for r in caplog.records)


# --- Document preview ---------------------------------------------------------------------------

def test_document_preview_limit_and_row_count(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        bank = next(d for d in c.get("/api/documents").json() if d["name"] == "Bank_Apr-Oct.csv")
        total = bank["row_count"]
        assert total > 50
        default = c.get(f"/api/documents/{bank['id']}").json()
        assert len(default["transactions"]) == 50 and default["row_count"] == total
        full = c.get(f"/api/documents/{bank['id']}?limit=1000").json()
        assert len(full["transactions"]) == total and full["row_count"] == total
        assert len(c.get(f"/api/documents/{bank['id']}?limit=1").json()["transactions"]) == 1
        for bad in (0, 1001, "x"):
            assert c.get(f"/api/documents/{bank['id']}?limit={bad}").status_code == 422


def test_document_preview_reports_how_many_transactions_it_pages_through(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        card = next(d for d in c.get("/api/documents").json() if d["name"].startswith("Card_Activity"))
        body = c.get(f"/api/documents/{card['id']}").json()
        txns = db.get().execute("SELECT count(*) FROM transactions WHERE document_id = ?", [card["id"]]).fetchone()[0]
        assert body["transaction_count"] == txns
        assert body["row_count"] > body["transaction_count"]    # the card statement also holds its debt account


def test_a_failed_read_stores_a_generic_message_and_logs_the_real_one(tmp_path, monkeypatch, caplog):
    from finagent import ingest, routes_documents

    monkeypatch.setattr(db, "_con", db.connect(tmp_path / "failed.duckdb"))
    con = db.get()
    doc = ingest.create_document(con, "x.csv", "csv", "spending")

    def boom(*_args, **_kwargs):
        raise RuntimeError("/Users/someone/secret/path.csv: provider said sk-test-123")

    monkeypatch.setattr(ingest, "ingest", boom)
    with caplog.at_level(logging.ERROR):
        routes_documents._ingest_job(doc, tmp_path / "x.csv")
    stored = db.rows(con, "SELECT status, error FROM documents WHERE id = ?", [doc])[0]
    assert stored["status"] == "failed"
    assert "secret" not in stored["error"] and "sk-test" not in stored["error"]
    assert "provider said" in caplog.text


def test_a_negative_budget_cap_is_refused(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        assert c.post("/api/budget-caps", json=[{"category": "Groceries", "monthly_cap": -5}]).status_code == 422
