import { useCallback, useEffect, useRef, useState } from 'react'
import { api, pct, usd, type Doc, type DocPreview, type Mapping } from '../api'
import { MAX_PREVIEW, showAllLabel } from '../helpers'

const TYPE_LABEL: Record<string, string> = { spending: 'Spending', debt: 'Debt', income: 'Income', receipt: 'Receipt' }

function docMeta(d: Doc) {
  if (d.status === 'failed') return `Failed: ${d.error ?? 'unknown error'}`
  const unit = d.doc_type === 'debt' ? (d.row_count === 1 ? 'account' : 'accounts') : d.row_count === 1 ? 'row' : 'rows'
  return `${TYPE_LABEL[d.doc_type] ?? d.doc_type} · ${d.row_count} ${unit}`
}

export default function Documents() {
  const [docs, setDocs] = useState<Doc[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [preview, setPreview] = useState<DocPreview | null>(null)
  const [mapping, setMapping] = useState<Mapping | null>(null)
  const [over, setOver] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const file = useRef<HTMLInputElement>(null)
  const startedAt = useRef<Record<number, number>>({})
  const [, setTick] = useState(0)

  const loadDocs = useCallback(async () => {
    const list = await api.documents()
    setDocs(list)
    return list
  }, [])

  const open = useCallback(async (id: number) => {
    setSelected(id); setErr(null)
    const p = await api.document(id)
    setPreview(p)
    setMapping(p.mapping ?? null)
  }, [])

  useEffect(() => {
    loadDocs().then((list) => { if (list.length) open(list[0].id) })
  }, [loadDocs, open])

  // Poll while any Document is still being extracted, and tick once a second for the elapsed-time label.
  useEffect(() => {
    if (!docs.some((d) => d.status === 'extracting')) return
    docs.filter((d) => d.status === 'extracting').forEach((d) => { startedAt.current[d.id] ??= Date.now() })
    const clock = setInterval(() => setTick((t) => t + 1), 1000)
    const id = setInterval(async () => {
      const list = await loadDocs()
      const sel = list.find((d) => d.id === selected)
      if (sel && sel.status !== 'extracting') open(sel.id)
    }, 1500)
    return () => { clearInterval(id); clearInterval(clock) }
  }, [docs, selected, loadDocs, open])

  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    setErr(null)
    for (const f of Array.from(files)) {
      try {
        const { id } = await api.upload(f)
        await loadDocs()
        setSelected(id)
        setPreview(null)
      } catch (e) {
        setErr(`${f.name}: ${(e as Error).message}`)
      }
    }
  }

  const reextract = async () => {
    if (!selected || !mapping) return
    setBusy(true)
    try { const p = await api.reextract(selected, mapping); setPreview(p); await loadDocs() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const confirm = async () => {
    if (!selected) return
    await api.confirm(selected)
    await loadDocs()
    await open(selected)
  }

  const remove = async () => {
    if (!selected) return
    await api.remove(selected)
    const list = await loadDocs()
    setPreview(null)
    if (list.length) open(list[0].id)
  }

  const fix = async (row: number, category: string) => {
    if (!selected) return
    const t = await api.fixRow(selected, row, category)
    setPreview((p) => p && { ...p, needs_review: p.needs_review - 1, transactions: p.transactions.map((x) => (x.row_no === row ? t : x)) })
  }

  const loadAll = async () => {
    if (!selected || !preview) return
    setBusy(true)
    try { setPreview(await api.document(selected, Math.min(total, MAX_PREVIEW))); setShowAll(true) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const doc = docs.find((d) => d.id === selected)
  const total = preview?.transaction_count ?? doc?.row_count ?? 0   // the table lists transactions; row_count also counts debts and payslips
  const elapsed = doc ? Math.round((Date.now() - (startedAt.current[doc.id] ?? Date.now())) / 1000) : 0
  const rows = preview ? (showAll ? preview.transactions : preview.transactions.slice(0, 8)) : []
  const isTable = doc && (doc.file_type === 'csv' || doc.file_type === 'xlsx')
  const selectField = (key: keyof Mapping, label: string) => (
    <label>{label}
      <select value={mapping?.[key] ?? ''} onChange={(e) => setMapping((m) => m && { ...m, [key]: e.target.value || null })}>
        <option value="">—</option>
        {preview?.columns.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
    </label>
  )

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="title">Your financial data</h1>
          <p className="muted" style={{ margin: '6px 0 0' }}>Add statements, payslips or loan schedules. Agents read only what's here, and every insight cites the rows it used.</p>
        </div>
      </div>
      {err && <p className="error">{err}</p>}

      <div className="sources">
        <div>
          <div className={`drop ${over ? 'over' : ''}`} onClick={() => file.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files) }}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M12 16V4M7 9l5-5 5 5M4 20h16" /></svg>
            <p style={{ fontWeight: 600, margin: '10px 0 4px' }}>Drop Documents or browse</p>
            <p className="muted" style={{ fontSize: 13, margin: 0 }}>PDF, CSV, XLSX · bank, card, payslip, loan</p>
            <input ref={file} type="file" multiple hidden accept=".csv,.xlsx,.pdf" onChange={(e) => upload(e.target.files)} />
          </div>
          <div className="doc-list">
            <div className="card">
              <h3>Documents ({docs.length})</h3>
              {docs.map((d) => (
                <div key={d.id} className={`doc ${d.id === selected ? 'on' : ''}`} onClick={() => open(d.id)}>
                  <span className={`ft ${d.file_type}`}>{d.file_type}</span>
                  <div><div className="name">{d.name}</div><div className="meta">{d.status === 'extracting' ? 'Reading…' : docMeta(d)}</div></div>
                  <span className={`st ${d.status}`}>{d.status === 'extracting' ? <><span className="spinner" /> Extracting</> : d.status === 'review' ? 'Review' : d.status === 'failed' ? 'Failed' : 'Ready'}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="card">
          {!doc && <p className="muted">Select a Document.</p>}
          {doc && doc.status === 'extracting' && (
            <div>
              <h3 style={{ marginBottom: 6 }}><span className="spinner" /> Reading {doc.name} · {elapsed} s</h3>
              <p className="muted" style={{ margin: 0 }}>
                {doc.file_type === 'csv' || doc.file_type === 'xlsx'
                  ? 'Parsing the table and tagging categories…'
                  : 'Claude is reading every page, extracting each row into a typed table, then tagging categories. A long statement takes about a minute.'}
              </p>
            </div>
          )}
          {doc && preview && doc.status !== 'extracting' && (
            <>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
                <div>
                  <h3 style={{ marginBottom: 2 }}>{doc.name} · extracted table</h3>
                  <div className="muted" style={{ fontSize: 13 }}>
                    {doc.status === 'review' ? (isTable ? 'Check the column mapping, then confirm. ' : 'Check the flagged Rows, then confirm. ') : 'Indexed and visible to agents. '}
                    {preview.transactions.length > 0 && 'Categories were auto-tagged for the Budget Coach.'}
                  </div>
                </div>
                <div className="row-actions" style={{ flex: 'none' }}>
                  <button className="btn" onClick={remove}>Delete</button>
                  {isTable && <button className="btn" onClick={reextract} disabled={busy}>Re-extract</button>}
                  {doc.status !== 'ready' && <button className="btn primary" onClick={confirm}>Confirm &amp; index</button>}
                </div>
              </div>

              {isTable && mapping && mapping.doc_type !== 'debt' && (
                <div className="mapping">
                  {selectField('date', 'Date column')}
                  {selectField('description', 'Description')}
                  {mapping.debit || mapping.credit
                    ? <>{selectField('debit', 'Debit (money out)')}{selectField('credit', 'Credit (money in)')}</>
                    : selectField('amount', 'Amount')}
                  <label>Document type
                    <select value={mapping.doc_type} onChange={(e) => setMapping({ ...mapping, doc_type: e.target.value as Mapping['doc_type'] })}>
                      {(['spending', 'debt'] as const).map((k) => <option key={k} value={k}>{TYPE_LABEL[k]}</option>)}
                    </select>
                  </label>
                </div>
              )}

              {preview.transactions.length > 0 && (
                <>
                  <table className="rows" style={{ marginTop: 16 }}>
                    <thead><tr><th>#</th><th>Date</th><th>Description</th><th>Category</th><th style={{ textAlign: 'right' }}>Amount (USD)</th><th>Confidence</th></tr></thead>
                    <tbody>
                      {rows.map((t) => (
                        <tr key={t.row_no} className={t.needs_review ? 'review' : ''}>
                          <td className="mono">{t.row_no}</td>
                          <td className="mono">{new Date(t.date + 'T00:00:00').toLocaleDateString('en-US', { day: '2-digit', month: 'short' })}</td>
                          <td>{t.description}</td>
                          <td>{t.needs_review ? (
                            <select value={t.category} onChange={(e) => fix(t.row_no, e.target.value)}>
                              {preview.categories.map((c) => <option key={c}>{c}</option>)}
                            </select>
                          ) : t.category}</td>
                          <td className={`num ${t.amount > 0 ? 'pos' : ''}`}>{t.amount > 0 ? '+' : ''}{t.amount.toLocaleString('en-US', { minimumFractionDigits: 2 })}</td>
                          <td className={t.needs_review ? 'conf-review' : ''}>{t.needs_review ? 'Review' : t.confidence >= 0.9 ? 'High' : 'Medium'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12, fontSize: 13 }} className="muted">
                    <span>Showing {rows.length} of {total} rows · {preview.needs_review} need review</span>
                    <span>
                      {preview.transactions.length > 8 && <button className="btn small" style={{ border: 0, color: 'var(--green)', textDecoration: 'underline', background: 'none' }} onClick={() => setShowAll(!showAll)}>{showAll ? 'Show fewer' : 'Show more rows'}</button>}
                      {showAllLabel(preview.transactions.length, total) && <button className="btn small" style={{ border: 0, color: 'var(--green)', textDecoration: 'underline', background: 'none' }} onClick={loadAll} disabled={busy}>{showAllLabel(preview.transactions.length, total)}</button>}
                    </span>
                  </div>
                </>
              )}

              {preview.debts.length > 0 && (
                <table className="rows" style={{ marginTop: 16 }}>
                  <thead><tr><th>#</th><th>Account</th><th>Type</th><th style={{ textAlign: 'right' }}>Balance</th><th style={{ textAlign: 'right' }}>APR</th><th style={{ textAlign: 'right' }}>Min / month</th><th>Due day</th></tr></thead>
                  <tbody>{preview.debts.map((d) => (
                    <tr key={d.row_no}><td className="mono">{d.row_no}</td><td>{d.name}</td><td>{d.kind}</td><td className="num">{usd(d.balance)}</td><td className="num">{pct(d.apr)}</td><td className="num">{usd(d.min_payment)}</td><td className="mono">{d.due_day ?? '—'}</td></tr>
                  ))}</tbody>
                </table>
              )}

              {preview.payslips.map((p) => (
                <table className="rows" style={{ marginTop: 16 }} key={p.period}>
                  <thead><tr><th>Period</th><th>Employer</th><th style={{ textAlign: 'right' }}>Gross</th><th style={{ textAlign: 'right' }}>Net</th></tr></thead>
                  <tbody><tr><td>{p.period}</td><td>{p.employer}</td><td className="num">{usd(p.gross, 2)}</td><td className="num">{usd(p.net, 2)}</td></tr></tbody>
                </table>
              ))}

              {doc.doc_type === 'receipt' && preview.transactions.length === 0 && (
                <p className="muted" style={{ marginTop: 16 }}>Receipts are kept as evidence. The purchase is already counted once on the card statement.</p>
              )}
            </>
          )}
        </div>
      </div>

      <div className="privacy">
        <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="#8fd3b0" strokeWidth="1.8"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /></svg>
        <div>
          <b>Your data stays yours</b>
          <p>Account numbers are masked in every stored row before agents see them. Claude reads a PDF once to extract its Rows. Delete a Document and its Rows, and any Insight that cites it, are removed.</p>
        </div>
      </div>
    </>
  )
}
