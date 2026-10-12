from finagent import db, seed


def _counts(con):
    return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("documents", "transactions", "debt_accounts", "payslips")}


def test_load_writes_five_documents_with_their_rows(tmp_path):
    con = db.connect(tmp_path / "seed.duckdb")
    seed.load(con)
    docs = db.rows(con, "SELECT name, status, row_count FROM documents ORDER BY id")
    assert [d["name"] for d in docs] == ["Bank_Apr-Oct.csv", "Card_Activity_Apr-Oct.pdf", "Loan_Schedule.xlsx",
                                         "Payslip_Sep.pdf", "Receipt_scan.jpg"]
    assert all(d["status"] == "ready" for d in docs)
    counts = _counts(con)
    assert counts["documents"] == 5 and counts["payslips"] == 1 and counts["debt_accounts"] == 4
    assert counts["transactions"] > 200
    assert sum(d["row_count"] for d in docs) == counts["transactions"] + counts["debt_accounts"] + counts["payslips"]


def test_load_twice_does_not_duplicate(tmp_path):
    con = db.connect(tmp_path / "seed2.duckdb")
    seed.load(con)
    once = _counts(con)
    seed.load(con)
    assert _counts(con) == once


def test_the_default_extra_payment_in_the_seed_comes_from_config(tmp_path, monkeypatch):
    from finagent import config

    monkeypatch.setattr(config, "DEFAULT_EXTRA_MONTHLY", 750)
    con = db.connect(tmp_path / "seedcfg.duckdb")
    seed.load(con)
    assert con.execute("SELECT value FROM settings WHERE key = 'extra_monthly'").fetchone()[0] == "750"
