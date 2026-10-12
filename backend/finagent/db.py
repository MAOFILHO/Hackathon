"""DuckDB storage: tables for Documents and their Rows, plus the read-only views agents query."""

import json
import threading
from pathlib import Path

import duckdb

MAX_ROWS = 200

SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS ids;
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY, name VARCHAR, file_type VARCHAR, doc_type VARCHAR,
    status VARCHAR, row_count INTEGER, created_at TIMESTAMP, error VARCHAR, mapping VARCHAR);
CREATE TABLE IF NOT EXISTS row_fixes (document_id INTEGER, row_no INTEGER, category VARCHAR);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER, document_id INTEGER, row_no INTEGER, date DATE, description VARCHAR,
    amount DOUBLE, category VARCHAR, flow VARCHAR, confidence DOUBLE, needs_review BOOLEAN, page INTEGER);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS page INTEGER;
CREATE TABLE IF NOT EXISTS debt_accounts (
    id INTEGER, document_id INTEGER, row_no INTEGER, name VARCHAR, kind VARCHAR,
    balance DOUBLE, apr DOUBLE, min_payment DOUBLE, due_day INTEGER);
CREATE TABLE IF NOT EXISTS payslips (
    id INTEGER, document_id INTEGER, row_no INTEGER, period VARCHAR, employer VARCHAR,
    gross DOUBLE, net DOUBLE);
CREATE TABLE IF NOT EXISTS insights (
    id INTEGER, agent VARCHAR, title VARCHAR, body VARCHAR, action_kind VARCHAR,
    action_payload VARCHAR, sources VARCHAR, status VARCHAR, created_at TIMESTAMP, evidence VARCHAR);
CREATE TABLE IF NOT EXISTS budget_caps (category VARCHAR PRIMARY KEY, monthly_cap DOUBLE);
CREATE TABLE IF NOT EXISTS settings (key VARCHAR PRIMARY KEY, value VARCHAR);

CREATE OR REPLACE VIEW v_transactions AS
    SELECT t.date, t.description, t.amount, t.category, t.flow, t.needs_review,
           d.name AS document, t.row_no, t.page
    FROM transactions t JOIN documents d ON d.id = t.document_id WHERE d.status = 'ready';
CREATE OR REPLACE VIEW v_debts AS
    SELECT a.name, a.kind, a.balance, a.apr, a.min_payment, a.due_day, d.name AS document, a.row_no
    FROM debt_accounts a JOIN documents d ON d.id = a.document_id WHERE d.status = 'ready';
CREATE OR REPLACE VIEW v_payslips AS
    SELECT p.period, p.employer, p.gross, p.net, d.name AS document, p.row_no
    FROM payslips p JOIN documents d ON d.id = p.document_id WHERE d.status = 'ready';
CREATE OR REPLACE VIEW v_budget_caps AS SELECT * FROM budget_caps;
"""

AGENT_VIEWS = {
    "v_transactions": "date, description, amount (negative = money out), category, "
                      "flow (income|spending|transfer|debt_service), needs_review, document, row_no, page (PDF page, may be null)",
    "v_debts": "name, kind, balance, apr (0.299 = 29.9%), min_payment, due_day, document, row_no",
    "v_payslips": "period, employer, gross, net, document, row_no",
    "v_budget_caps": "category, monthly_cap",
}


class AgentSQLError(ValueError):
    pass


ALLOWED_TABLE_FUNCTIONS = {"range", "generate_series"}
SIDE_EFFECT_FUNCTIONS = {"nextval", "currval", "setseed"}  # a SELECT that moves the id counter or the random seed


def _referenced_tables(con, sql: str) -> tuple[set[str], set[str], set[str], set[str]]:
    """(base tables, CTE names, table functions, scalar function calls) a SELECT touches, from DuckDB's own parse tree."""
    ast = json.loads(con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
    if ast.get("error"):
        raise AgentSQLError(f"Could not parse SQL: {ast.get('error_message')}")
    tables: set[str] = set()
    ctes: set[str] = set()
    functions: set[str] = set()
    calls: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "BASE_TABLE":
                tables.add(node["table_name"].lower())
            if node.get("type") == "TABLE_FUNCTION":
                functions.add(str(node.get("function", {}).get("function_name", "")).lower())
            if node.get("type") == "FUNCTION":
                calls.add(str(node.get("function_name", "")).lower())
            for entry in node.get("cte_map", {}).get("map", []):
                ctes.add(entry["key"].lower())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(ast)
    return tables, ctes, functions, calls


def connect(path: Path | str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    con.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS error VARCHAR")
    con.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS mapping VARCHAR")
    con.execute("ALTER TABLE insights ADD COLUMN IF NOT EXISTS evidence VARCHAR")  # databases created before Why? existed
    _advance_ids(con)
    con.execute("SET enable_external_access = false")
    return con


def _advance_ids(con) -> None:
    """Move the id counter past every id in use. After an abrupt stop the counter can restart below existing ids."""
    highest = con.execute("""SELECT greatest(
        coalesce((SELECT max(id) FROM documents), 0), coalesce((SELECT max(id) FROM transactions), 0),
        coalesce((SELECT max(id) FROM debt_accounts), 0), coalesce((SELECT max(id) FROM payslips), 0),
        coalesce((SELECT max(id) FROM insights), 0))""").fetchone()[0]
    current = con.execute("SELECT nextval('ids')").fetchone()[0]
    if current <= highest:
        con.execute("SELECT max(nextval('ids')) FROM range(?)", [highest - current + 1])


_con: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()


def get() -> duckdb.DuckDBPyConnection:
    """A cursor on the app database; one per call so threads don't share state."""
    global _con
    with _lock:
        if _con is None:
            data = Path(__file__).resolve().parent.parent / "data"
            data.mkdir(exist_ok=True)
            _con = connect(data / "finagent.duckdb")
        return _con.cursor()


def next_id(con) -> int:
    return con.execute("SELECT nextval('ids')").fetchone()[0]


def rows(con, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def agent_sql(con, sql: str) -> list[dict]:
    """Run an agent-written query: one SELECT only, no file access, capped rows."""
    try:
        stmts = con.extract_statements(sql)
    except duckdb.Error as e:
        raise AgentSQLError(f"Could not parse SQL: {e}") from e
    if len(stmts) != 1 or stmts[0].type != duckdb.StatementType.SELECT:
        raise AgentSQLError("Only a single SELECT statement is allowed.")
    tables, ctes, functions, calls = _referenced_tables(con, sql)
    hidden = (tables - ctes) - set(AGENT_VIEWS)
    if hidden or not functions <= ALLOWED_TABLE_FUNCTIONS:
        raise AgentSQLError("Query only these views: " + ", ".join(AGENT_VIEWS))
    if calls & SIDE_EFFECT_FUNCTIONS:
        raise AgentSQLError("That function changes database state and is not allowed.")
    try:
        cur = con.execute(sql)
    except duckdb.Error as e:
        raise AgentSQLError(str(e)) from e
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchmany(MAX_ROWS)]


def clear_rows(con, doc_id: int) -> None:
    """Remove every Row a Document produced, keeping the Document itself."""
    con.execute("DELETE FROM transactions WHERE document_id = ?", [doc_id])
    con.execute("DELETE FROM debt_accounts WHERE document_id = ?", [doc_id])
    con.execute("DELETE FROM payslips WHERE document_id = ?", [doc_id])


def delete_document(con, doc_id: int) -> None:
    clear_rows(con, doc_id)
    con.execute("DELETE FROM row_fixes WHERE document_id = ?", [doc_id])
    found = con.execute("SELECT name FROM documents WHERE id = ?", [doc_id]).fetchone()
    if found:  # Insights that cite the Document would show numbers the user just deleted
        for insight_id, sources in con.execute("SELECT id, sources FROM insights").fetchall():
            if any(src.get("document") == found[0] for src in json.loads(sources or "[]")):
                con.execute("DELETE FROM insights WHERE id = ?", [insight_id])
    con.execute("DELETE FROM documents WHERE id = ?", [doc_id])
