import pytest
from fastapi.testclient import TestClient

from finagent import db, ingest, llm, routes_documents, seed
from finagent.api import app

CSV = ("Txn Date,Narration,Debit,Credit\n"
       "2026-10-01,Corner Coffee cafe,5.50,\n"
       "2026-10-02,Mystery vendor,20.00,\n"
       "2026-10-03,Salary credit - Acme,,1000.00\n")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(db, "_con", db.connect(tmp_path / "docs.duckdb"))
    monkeypatch.setattr(routes_documents, "UPLOADS", tmp_path / "uploads")
    with TestClient(app) as c:
        yield c


def _upload(c, text=CSV, name="new.csv"):
    return c.post("/api/documents", files={"file": (name, text, "text/csv")}).json()["id"]


def test_csv_lifecycle_keeps_manual_fixes_and_ready_status_across_re_extract(client):
    c = client
    doc = _upload(c)
    prev = c.get(f"/api/documents/{doc}").json()
    assert prev["document"]["status"] == "review"
    assert prev["needs_review"] == 1  # "Mystery vendor" is Other with low confidence

    fixed = c.patch(f"/api/documents/{doc}/rows/2", json={"category": "Shopping"}).json()
    assert fixed["category"] == "Shopping" and fixed["confidence"] == 1.0
    assert c.post(f"/api/documents/{doc}/confirm").json()["status"] == "ready"

    again = c.post(f"/api/documents/{doc}/reextract", json=prev["mapping"]).json()

    assert again["document"]["status"] == "ready"
    by_row = {t["row_no"]: t for t in again["transactions"]}
    assert by_row[2]["category"] == "Shopping" and by_row[2]["needs_review"] is False
    assert again["needs_review"] == 0

    assert c.delete(f"/api/documents/{doc}").status_code == 200
    assert c.get(f"/api/documents/{doc}").status_code == 404
    assert db.get().execute("SELECT count(*) FROM row_fixes WHERE document_id = ?", [doc]).fetchone()[0] == 0


def test_re_extract_of_a_document_in_review_stays_in_review(client):
    doc = _upload(client)
    mapping = client.get(f"/api/documents/{doc}").json()["mapping"]
    assert client.post(f"/api/documents/{doc}/reextract", json=mapping).json()["document"]["status"] == "review"


def test_re_extract_saves_the_chosen_mapping_and_get_returns_it(client):
    csv = "Txn Date,Narration,Amount,Fee\n2026-10-01,Corner Coffee cafe,-5.50,-1.25\n"
    doc = _upload(client, csv)
    guessed = client.get(f"/api/documents/{doc}").json()["mapping"]
    assert guessed["amount"] == "Amount"

    chosen = {**guessed, "amount": "Fee"}
    again = client.post(f"/api/documents/{doc}/reextract", json=chosen).json()

    assert again["transactions"][0]["amount"] == -1.25
    assert client.get(f"/api/documents/{doc}").json()["mapping"]["amount"] == "Fee"


def test_both_debit_and_credit_columns_can_be_chosen(client):
    doc = _upload(client)
    mapping = client.get(f"/api/documents/{doc}").json()["mapping"]
    assert (mapping["debit"], mapping["credit"]) == ("Debit", "Credit")
    swapped = {**mapping, "credit": None}
    again = client.post(f"/api/documents/{doc}/reextract", json=swapped).json()
    assert sorted(t["amount"] for t in again["transactions"]) == [-20.0, -5.5, 0.0]  # salary row lost its credit column


@pytest.mark.parametrize("doc_type", ["income", "receipt"])
def test_re_extract_rejects_mapping_doc_types_a_table_cannot_be(client, doc_type):
    doc = _upload(client)
    mapping = client.get(f"/api/documents/{doc}").json()["mapping"]
    r = client.post(f"/api/documents/{doc}/reextract", json={**mapping, "doc_type": doc_type})
    assert r.status_code == 422


def test_failure_is_stored_in_error_and_doc_type_is_kept(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("cannot read this file")

    monkeypatch.setattr(ingest, "ingest", boom)
    doc = _upload(client)

    listed = {d["id"]: d for d in client.get("/api/documents").json()}[doc]
    assert listed["status"] == "failed"
    assert listed["doc_type"] == "spending"
    assert listed["error"] == "cannot read this file"
    assert "mapping" not in listed


def test_pdf_upload_is_accepted_without_an_api_key_and_fails_on_the_document(client, monkeypatch):
    def boom(path):
        raise RuntimeError("no key")

    monkeypatch.setattr(ingest, "extract_file", boom)
    r = client.post("/api/documents", files={"file": ("s.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 200
    listed = {d["id"]: d for d in client.get("/api/documents").json()}[r.json()["id"]]
    assert listed["status"] == "failed" and listed["error"] == "no key"


@pytest.mark.parametrize("name", ["x.exe", "scan.jpg", "scan.jpeg", "scan.png", "old.xls"])
def test_unsupported_file_types_are_rejected(client, name):
    r = client.post("/api/documents", files={"file": (name, b"data", "application/octet-stream")})
    assert r.status_code == 400


def test_seed_load_twice_gives_the_same_counts_and_september_income(tmp_path):
    con = db.connect(tmp_path / "seed.duckdb")

    def counts():
        return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("documents", "transactions", "debt_accounts", "payslips")}

    seed.load(con)
    first = counts()
    seed.load(con)

    assert counts() == first and first["documents"] == 5
    sep_income = con.execute("""SELECT sum(amount) FROM transactions
                                WHERE flow = 'income' AND date BETWEEN '2026-09-01' AND '2026-09-30'""").fetchone()[0]
    assert sep_income == 6850


# --- Integration: shared constants and sample files ---------------------------------------

def test_seeding_does_not_rewrite_existing_sample_files(tmp_path):
    from finagent import db, seed

    con = db.connect(tmp_path / "s.duckdb")
    seed.load(con)
    before = {p.name: p.stat().st_mtime_ns for p in seed.SAMPLES.iterdir()}
    seed.load(con)
    after = {p.name: p.stat().st_mtime_ns for p in seed.SAMPLES.iterdir()}
    assert before == after


def test_one_definition_of_each_shared_constant():
    from finagent import agents, config, finance

    assert finance.SAVINGS_TRANSFER_PATTERN is config.SAVINGS_TRANSFER_PATTERN
    assert agents._ctx()["user"] == config.USER_NAME
    assert f"{config.EMERGENCY_FUND_MONTHS} months" in agents.SPECIALISTS["Savings Strategist"][0]


def test_error_messages_say_document_not_file(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from finagent import db, llm
    from finagent.api import app

    monkeypatch.setattr(llm, "available", lambda: False)
    monkeypatch.setattr(db, "_con", db.connect(tmp_path / "m.duckdb"))
    with TestClient(app) as c:
        bad = c.post("/api/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
        assert bad.status_code == 400 and "Unsupported Document type" in bad.json()["detail"]
        assert c.post("/api/payoff-strategy", json={"strategy": "avalanche", "extra_monthly": 100}).status_code == 200
        assert c.post("/api/plan/payoff", json={"strategy": "avalanche", "extra_monthly": 100}).status_code == 404
