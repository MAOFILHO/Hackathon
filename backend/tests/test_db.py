import pytest

from finagent import db


@pytest.fixture()
def con(tmp_path):
    c = db.connect(tmp_path / "t.duckdb")
    c.execute("INSERT INTO documents (id, name, file_type, doc_type, status, row_count, created_at) "
              "VALUES (1, 'Bank.csv', 'csv', 'spending', 'ready', 1, now())")
    c.execute("INSERT INTO transactions (id, document_id, row_no, date, description, amount, category, flow, confidence, needs_review) "
              "VALUES (1, 1, 1, DATE '2026-09-01', 'Shop', -10, 'Shopping', 'spending', 0.9, false)")
    return c


def test_agent_sql_reads_views(con):
    rows = db.agent_sql(con, "SELECT category, sum(amount) AS total FROM v_transactions GROUP BY 1")
    assert rows == [{"category": "Shopping", "total": -10.0}]


@pytest.mark.parametrize("sql", [
    "DELETE FROM transactions",
    "SELECT 1; DROP TABLE transactions",
    "UPDATE transactions SET amount = 0",
    "SELECT * FROM read_text('/etc/hosts')",
])
def test_agent_sql_rejects_writes_and_file_access(con, sql):
    with pytest.raises(db.AgentSQLError):
        db.agent_sql(con, sql)
    assert con.execute("SELECT count(*) FROM transactions").fetchone()[0] == 1


def test_agent_sql_caps_rows(con):
    rows = db.agent_sql(con, "SELECT * FROM range(1000)")
    assert len(rows) == db.MAX_ROWS


def test_deleting_document_deletes_its_rows(con):
    db.delete_document(con, 1)
    assert con.execute("SELECT count(*) FROM transactions").fetchone()[0] == 0


@pytest.mark.parametrize("sql", [
    "SELECT * FROM transactions",
    "SELECT * FROM main.documents",
    "SELECT (SELECT count(*) FROM insights)",
    "SELECT * FROM (SELECT * FROM debt_accounts) x",
    "SELECT * FROM duckdb_tables()",
    "SELECT * FROM information_schema.tables",
])
def test_agent_sql_only_reads_the_approved_views(con, sql):
    with pytest.raises(db.AgentSQLError):
        db.agent_sql(con, sql)


def test_agent_sql_allows_ctes_and_joins_over_views(con):
    rows = db.agent_sql(con, """WITH s AS (SELECT category, sum(amount) AS t FROM v_transactions GROUP BY 1)
                               SELECT s.category, s.t FROM s JOIN v_budget_caps USING (category)
                               UNION ALL SELECT category, t FROM s""")
    assert rows == [{"category": "Shopping", "t": -10.0}]


def test_deleting_a_document_removes_insights_that_cite_it(con):
    con.execute("INSERT INTO insights (id, agent, title, body, action_kind, action_payload, sources, status, created_at) "
                "VALUES (1, 'Budget Coach', 't', 'b', 'set_cap', '{}', ?, 'new', now())",
                ['[{"document": "Bank.csv", "rows": "rows 1-1", "count": 1}]'])
    con.execute("INSERT INTO insights VALUES (2, 'Debt Analyzer', 't', 'b', 'add_to_plan', '{}', ?, 'new', now(), NULL)",
                ['[{"document": "Loans.xlsx", "rows": "rows 1-3", "count": 3}]'])
    db.delete_document(con, 1)
    assert [r[0] for r in con.execute("SELECT id FROM insights").fetchall()] == [2]


def test_connect_adds_error_and_mapping_columns_to_an_older_database(tmp_path):
    import duckdb
    path = tmp_path / "old.duckdb"
    old = duckdb.connect(str(path))
    old.execute("""CREATE TABLE documents (
        id INTEGER PRIMARY KEY, name VARCHAR, file_type VARCHAR, doc_type VARCHAR,
        status VARCHAR, row_count INTEGER, created_at TIMESTAMP)""")
    old.execute("INSERT INTO documents VALUES (1, 'Bank.csv', 'csv', 'spending', 'ready', 1, now())")
    old.close()

    con = db.connect(path)

    row = db.rows(con, "SELECT name, error, mapping FROM documents")[0]
    assert row == {"name": "Bank.csv", "error": None, "mapping": None}
    con.execute("INSERT INTO row_fixes VALUES (1, 3, 'Groceries')")


def test_delete_document_forgets_its_row_fixes(con):
    con.execute("INSERT INTO row_fixes VALUES (1, 1, 'Groceries')")
    db.delete_document(con, 1)
    assert con.execute("SELECT count(*) FROM row_fixes").fetchone()[0] == 0


def test_new_ids_start_above_every_existing_id_after_a_reopen(tmp_path):
    path = tmp_path / "ids.duckdb"
    first = db.connect(path)
    first.execute("INSERT INTO documents (id, name, file_type, doc_type, status, row_count, created_at) "
                  "VALUES (5000, 'Old.csv', 'csv', 'spending', 'ready', 0, now())")
    first.execute("INSERT INTO insights (id, agent, title, body, action_kind, action_payload, sources, status, created_at) "
                  "VALUES (7000, 'Budget Coach', 't', 'b', 'set_cap', '{}', '[]', 'new', now())")
    first.close()  # the id counter is not saved with these rows, as after an abrupt stop

    second = db.connect(path)
    assert db.next_id(second) > 7000
    assert len({db.next_id(second) for _ in range(5)}) == 5


@pytest.mark.parametrize("sql", [
    "SELECT nextval('ids')",
    "SELECT NEXTVAL('ids')",
    "SELECT main.nextval('ids')",
    "SELECT currval('ids')",
    "SELECT setseed(0.5)",
    "SELECT category FROM v_transactions WHERE nextval('ids') > 0",
    "SELECT * FROM range(3) WHERE EXISTS (SELECT nextval('ids'))",
])
def test_agent_sql_rejects_side_effecting_functions(con, sql):
    before = con.execute("SELECT nextval('ids')").fetchone()[0]
    with pytest.raises(db.AgentSQLError):
        db.agent_sql(con, sql)
    assert con.execute("SELECT nextval('ids')").fetchone()[0] == before + 1  # the id counter did not move
