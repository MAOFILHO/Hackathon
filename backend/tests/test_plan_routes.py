from fastapi.testclient import TestClient

from finagent import db, llm
from finagent.api import app


def test_custom_payoff_order_with_an_unknown_account_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(db, "_con", db.connect(tmp_path / "plan.duckdb"))
    with TestClient(app) as c:
        bad = c.post("/api/payoff-strategy", json={"strategy": "custom", "extra_monthly": 500, "order": ["Nope"]})
        assert bad.status_code == 422 and "Nope" in bad.text
        assert c.post("/api/payoff-strategy", json={"strategy": "bogus", "extra_monthly": 500}).status_code == 422
        assert c.get("/api/dashboard").json()["payoff"]["strategy"] == "avalanche"  # nothing was saved
