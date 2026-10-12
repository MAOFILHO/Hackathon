"""Document routes: upload, review, re-extract, confirm, fix a Row, delete."""

import json
import logging
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel

from . import db, ingest, seed
from .schemas import CATEGORIES

log = logging.getLogger(__name__)
READ_FAILED = "This Document could not be read. Check the file and try again."

router = APIRouter(prefix="/api")
UPLOADS = Path(__file__).resolve().parent.parent / "data" / "uploads"
ALLOWED = {".csv": "csv", ".xlsx": "xlsx", ".pdf": "pdf"}
MAX_BYTES = 20 * 1024 * 1024


def _path(doc: dict) -> Path | None:
    for p in (UPLOADS / f"{doc['id']}_{doc['name']}", seed.SAMPLES / doc["name"]):
        if p.exists():
            return p
    return None


def _doc(con, doc_id: int) -> dict:
    found = db.rows(con, "SELECT * FROM documents WHERE id = ?", [doc_id])
    if not found:
        raise HTTPException(404, "Document not found")
    return found[0]


# --- Documents --------------------------------------------------------------------------

@router.get("/documents")
def documents():
    return jsonable_encoder(db.rows(db.get(), f"SELECT {ingest.DOC_COLUMNS} FROM documents ORDER BY created_at, id"))


def _ingest_job(doc_id: int, path: Path, mapping: ingest.Mapping | None = None, status: str = "review") -> None:
    con = db.get()
    had_rows = con.execute("SELECT row_count FROM documents WHERE id = ?", [doc_id]).fetchone()[0] > 0
    con.execute("BEGIN TRANSACTION")  # all or nothing: a failed read must not leave a Document without its Rows
    try:
        ingest.ingest(con, doc_id, path, mapping, status)
        con.execute("COMMIT")
    except Exception as e:
        con.execute("ROLLBACK")
        log.exception("Reading Document %s failed", doc_id)
        message = READ_FAILED
        if had_rows:  # keep the Rows it already had and say why the new read failed
            con.execute("UPDATE documents SET status = ?, error = ? WHERE id = ?", [status, message, doc_id])
        else:
            con.execute("UPDATE documents SET status = 'failed', error = ? WHERE id = ?", [message, doc_id])


@router.post("/documents")
async def upload(file: UploadFile, background: BackgroundTasks):
    name = Path(file.filename or "upload").name
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, f"Unsupported Document type {suffix}. Use CSV, XLSX or PDF.")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "Document is larger than 20 MB")
    con = db.get()
    doc_id = ingest.create_document(con, name, ALLOWED[suffix], "spending")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    path = UPLOADS / f"{doc_id}_{name}"
    path.write_bytes(data)
    background.add_task(_ingest_job, doc_id, path)
    return {"id": doc_id, "status": "extracting"}


@router.get("/documents/{doc_id}")
def document(doc_id: int, limit: Annotated[int, Query(ge=1, le=1000)] = 50):
    con = db.get()
    doc = _doc(con, doc_id)
    out = ingest.preview(con, doc_id, _path(doc), limit)
    out["row_count"] = doc["row_count"]  # all Rows in the Document (transactions, debts, payslips), not just the ones previewed
    out["transaction_count"] = con.execute("SELECT count(*) FROM transactions WHERE document_id = ?", [doc_id]).fetchone()[0]
    if doc["file_type"] in ("csv", "xlsx") and (p := _path(doc)):
        out["mapping"] = (json.loads(doc["mapping"]) if doc["mapping"]
                          else ingest.guess_mapping(ingest.read_table(p)).model_dump())
    out["categories"] = CATEGORIES
    return jsonable_encoder(out)


@router.post("/documents/{doc_id}/reextract")
def reextract(doc_id: int, mapping: ingest.Mapping | None = None):
    con = db.get()
    doc = _doc(con, doc_id)
    if doc["file_type"] not in ("csv", "xlsx", "pdf"):
        raise HTTPException(409, "This Document cannot be read again")
    path = _path(doc)
    if not path:
        raise HTTPException(409, "Original Document is not available to re-extract")
    if doc["status"] != "ready":  # a ready Document stays visible to agents until the new read commits
        con.execute("UPDATE documents SET status = 'extracting' WHERE id = ?", [doc_id])
    _ingest_job(doc_id, path, mapping, "ready" if doc["status"] == "ready" else "review")
    return document(doc_id)


@router.post("/documents/{doc_id}/confirm")
def confirm(doc_id: int):
    con = db.get()
    _doc(con, doc_id)
    con.execute("UPDATE documents SET status = 'ready' WHERE id = ?", [doc_id])
    return {"id": doc_id, "status": "ready"}


class RowFix(BaseModel):
    category: Literal[tuple(CATEGORIES)]  # type: ignore[valid-type]


@router.patch("/documents/{doc_id}/rows/{row_no}")
def fix_row(doc_id: int, row_no: int, fix: RowFix):
    con = db.get()
    t = ingest.fix_row(con, doc_id, row_no, fix.category)
    if not t:
        raise HTTPException(404, "Row not found")
    return t.model_dump(mode="json")


@router.delete("/documents/{doc_id}")
def delete(doc_id: int):
    con = db.get()
    doc = _doc(con, doc_id)
    db.delete_document(con, doc_id)
    if (p := UPLOADS / f"{doc_id}_{doc['name']}").exists():
        p.unlink()
    return {"deleted": doc_id}
