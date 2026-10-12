from fastapi.testclient import TestClient

from finagent import db, llm
from finagent.api import app


def test_offline_api_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(db, "_con", db.connect(tmp_path / "api.duckdb"))
    with TestClient(app) as c:
        dash = c.get("/api/dashboard").json()
        assert dash["debt"]["accounts"] == 4 and dash["income"]["value"] == 6850
        docs = c.get("/api/documents").json()
        assert len(docs) == 5
        r = c.post("/api/payoff-strategy", json={"strategy": "snowball", "extra_monthly": 1000}).json()
        assert r["strategy"] == "snowball" and r["accounts"][0]["name"].startswith("BNPL")
        assert c.post("/api/chat", json={"question": "hi"}).status_code == 503
        csv = "Txn Date,Narration,Amount\n2026-10-01,Corner Coffee cafe,-5.50\n"
        up = c.post("/api/documents", files={"file": ("new.csv", csv, "text/csv")}).json()
        prev = c.get(f"/api/documents/{up['id']}").json()
        assert prev["document"]["status"] == "review" and prev["transactions"][0]["category"] == "Dining out"
        assert c.post(f"/api/documents/{up['id']}/confirm").json()["status"] == "ready"
        assert c.delete(f"/api/documents/{up['id']}").status_code == 200
        assert c.post("/api/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400


def test_slider_keeps_a_custom_payoff_order(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(db, "_con", db.connect(tmp_path / "api2.duckdb"))
    with TestClient(app) as c:
        order = ["BNPL - ShopLater", "Car loan - AutoFin", "Personal loan - First Bank", "Visa Card ••••4417"]
        first = c.post("/api/payoff-strategy", json={"strategy": "custom", "extra_monthly": 500, "order": order}).json()
        assert first["accounts"][0]["name"] == "BNPL - ShopLater"
        slid = c.post("/api/payoff-strategy", json={"strategy": "custom", "extra_monthly": 900}).json()  # slider sends no order
        assert [a["name"] for a in slid["accounts"]] == order
        back = c.post("/api/payoff-strategy", json={"strategy": "avalanche", "extra_monthly": 900}).json()
        assert back["accounts"][0]["name"].startswith("Visa")


def test_chat_sends_the_earlier_turns_to_the_agents_and_bounds_them(tmp_path, monkeypatch):
    from finagent import agents

    captured = {}

    class FakeGraph:
        def stream(self, state, **_):
            captured.update(state)
            return iter(())

    monkeypatch.setattr(db, "_con", db.connect(tmp_path / "chat.duckdb"))
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(agents, "graph", FakeGraph())
    body = {"question": "What about snowball?",
            "history": [{"question": f"q{i}", "answer": f"a{i}"} for i in range(5)]}
    with TestClient(app) as c:
        assert c.post("/api/chat", json=body).status_code == 200
        assert c.post("/api/chat", json={"question": "hi", "history": [{"question": "q", "answer": "a"}] * 50}).status_code == 422
    assert captured["question"] == "What about snowball?"
    assert "q4" in captured["history"] and "q0" not in captured["history"]
