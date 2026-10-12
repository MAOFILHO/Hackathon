"""Citations: which Documents and Rows back the numbers an agent states."""

from . import db


_VIEW_TABLES = {"v_transactions": "transactions", "v_debts": "debt_accounts", "v_payslips": "payslips"}  # fixed; never user input
_SOURCES_SQL = ("SELECT d.name, count(*) n, min(x.row_no) lo, max(x.row_no) hi FROM {table} x "
                "JOIN documents d ON d.id = x.document_id WHERE d.status = 'ready' GROUP BY 1")


def sources_for(con, views: list[str]) -> list[dict]:
    """Which Documents feed the given views, with Row counts. Used for citations."""
    out = []
    for v in views:
        table = _VIEW_TABLES.get(v)
        if not table:
            continue
        for r in db.rows(con, _SOURCES_SQL.format(table=table)):
            out.append({"document": r["name"], "rows": f"rows {r['lo']}–{r['hi']}" if r["n"] > 1 else f"row {r['lo']}", "count": r["n"]})
    return out


def _runs(numbers) -> str:
    """4, 5, 9 -> '4–5, 9': consecutive numbers collapse into a range."""
    nums = sorted(set(numbers))
    parts, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(parts)


def format_rows(row_nos: list[int]) -> str:
    return f"{'row' if len(set(row_nos)) == 1 else 'rows'} {_runs(row_nos)}"


def _source(document: str, row_nos, pages) -> dict:
    row_nos, pages = sorted(set(row_nos)), sorted(set(pages))
    return {"document": document, "rows": format_rows(row_nos), "count": len(row_nos), "row_nos": row_nos,
            "pages": f"{'p.' if len(pages) == 1 else 'pp.'} {_runs(pages)}" if pages else None, "page_nos": pages}


def sources_from_result(rows: list[dict]) -> list[dict]:
    """Cite exactly the Rows a query returned, when it returned the document and row_no columns."""
    if not rows or not all("document" in r and "row_no" in r for r in rows):
        return []
    found: dict[str, tuple[set, set]] = {}
    for r in rows:
        if r["row_no"] is not None:
            nums, pages = found.setdefault(r["document"], (set(), set()))
            nums.add(r["row_no"])
            if r.get("page") is not None:
                pages.add(r["page"])
    return [_source(doc, nums, pages) for doc, (nums, pages) in found.items()]


def merge_sources(sources: list[dict]) -> list[dict]:
    """One citation per Document. Row lists are united; an aggregate citation (a whole range) wins over partial lists."""
    out: dict[str, dict] = {}
    for s in sources:
        mine = out.get(s["document"])
        if mine is None or "row_nos" not in mine:
            out[s["document"]] = mine or s
        elif "row_nos" not in s:
            out[s["document"]] = s
        else:
            out[s["document"]] = _source(s["document"], mine["row_nos"] + s["row_nos"], mine["page_nos"] + s["page_nos"])
    return list(out.values())
