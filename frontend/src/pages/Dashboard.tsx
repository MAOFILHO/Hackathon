import { useCallback, useEffect, useRef, useState } from 'react'
import { AGENT_CLASS, api, monthYear, pct, usd, type Dashboard as Dash, type Insight, type Strategy } from '../api'

const ago = (iso: string) => {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 60) return `${Math.max(1, mins)} min ago`
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`
  return 'Yesterday'
}

export default function Dashboard({ onAsk }: { onAsk: (q: string) => void }) {
  const [d, setD] = useState<Dash | null>(null)
  const [insights, setInsights] = useState<Insight[]>([])
  const [q, setQ] = useState('')
  const [extra, setExtra] = useState(500)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [editingOrder, setEditingOrder] = useState(false)
  const [order, setOrder] = useState<string[]>([])
  const [why, setWhy] = useState<number | null>(null)
  const debounce = useRef<number | undefined>(undefined)

  const load = useCallback(() => {
    api.dashboard().then((x) => { setD(x); setExtra(x.payoff.extra_monthly) }).catch((e) => setErr(e.message))
    api.insights().then(setInsights).catch((e) => setErr(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const setPayoff = (strategy: Strategy, value = extra) => {
    clearTimeout(debounce.current)
    debounce.current = window.setTimeout(() => {
      api.setPayoff(strategy, value).then((payoff) => setD((prev) => (prev ? { ...prev, payoff } : prev)))
    }, 150)
  }

  const refresh = async () => {
    setBusy(true); setErr(null)
    try { setInsights(await api.refreshInsights()) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  const act = async (i: Insight, action: 'add_to_plan' | 'dismiss') => {
    await api.actOnInsight(i.id, action)
    load()  // "Set cap" also changes the Budget Caps card
  }

  const toggleOrder = () => {
    if (!editingOrder && d) setOrder(d.payoff.accounts.map((a) => a.name))
    setEditingOrder(!editingOrder)
  }
  const move = (from: number, by: number) => setOrder((o) => {
    const next = [...o]
    const [x] = next.splice(from, 1)
    next.splice(from + by, 0, x)
    return next
  })
  const applyOrder = () => {
    api.setPayoff('custom', extra, order).then((payoff) => { setD((prev) => (prev ? { ...prev, payoff } : prev)); setEditingOrder(false) })
      .catch((e) => setErr(e.message))
  }

  if (!d) return <div className="muted">{err ?? 'Loading…'}</div>

  const max = Math.max(...d.cashflow.map((c) => Math.max(c.income, c.spending + c.debt_service)), 1)
  const firstName = d.user?.split(' ')[0]
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
  const longest = Math.max(...d.payoff.accounts.map((a) => (a.paid_off ? new Date(a.paid_off).getTime() : 0)), 1)
  const start = new Date(d.as_of).getTime()

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">{new Date(d.as_of + 'T00:00:00').toLocaleDateString('en-US', { month: 'long', year: 'numeric' })} · sample data</div>
          <h1 className="title">{greeting}{firstName ? `, ${firstName}` : ''}</h1>
        </div>
        <form className="ask" onSubmit={(e) => { e.preventDefault(); if (q.trim()) onAsk(q.trim()) }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder='Ask anything — "Can I afford a car loan in March?"' />
          <button className="btn primary">Ask</button>
        </form>
      </div>

      <div className="kpis">
        <div className="card kpi">
          <div className="label">Monthly income</div>
          <div className="value">{usd(d.income.value)}</div>
          <div className="note good">
            {d.income.note}
            {d.income.matches_bank != null && d.income.payslip_net != null &&
              (d.income.matches_bank ? ' · Matches payslip' : ` · Differs from payslip by ${usd(Math.abs(d.income.value - d.income.payslip_net))}`)}
          </div>
        </div>
        <div className="card kpi">
          <div className="label">Spending (30 days)</div>
          <div className="value">{usd(d.spending_30d.value)}</div>
          <div className={`note ${(d.spending_30d.vs_3mo_avg ?? 0) > 0 ? 'up' : 'good'}`}>
            {d.spending_30d.vs_3mo_avg == null ? '—' : `${d.spending_30d.vs_3mo_avg > 0 ? '▲' : '▼'} ${pct(Math.abs(d.spending_30d.vs_3mo_avg), 0)} vs 3-mo avg`}
          </div>
        </div>
        <div className="card kpi">
          <div className="label">Total debt</div>
          <div className="value">{usd(d.debt.total)}</div>
          <div className="note">{d.debt.accounts} accounts · avg {pct(d.debt.avg_apr)} APR</div>
        </div>
        <div className="card kpi">
          <div className="label">Savings rate</div>
          <div className="value">{pct(d.savings_rate.value)}</div>
          <div className="note">Target {pct(d.savings_rate.target, 0)} · {d.month_label}</div>
        </div>
      </div>

      <div className="dash">
        <div className="stack">
          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <h3>Cash flow · last 6 months</h3>
              <div className="legend"><span><i style={{ background: 'var(--green)' }} />Income</span><span><i style={{ background: 'var(--orange)' }} />Spending + Debt Service</span></div>
            </div>
            <div className="bars">
              {d.cashflow.map((c) => (
                <div className="bar-pair" key={c.month}>
                  <div className="bar inc" style={{ height: `${(c.income / max) * 100}%` }} data-v={usd(c.income)} />
                  <div className="bar spd" style={{ height: `${((c.spending + c.debt_service) / max) * 100}%` }} data-v={usd(c.spending + c.debt_service)} />
                </div>
              ))}
            </div>
            <div className="bar-labels">{d.cashflow.map((c) => <span key={c.month}>{c.month}</span>)}</div>
          </div>

          <div className="card" id="payoff">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <h3 style={{ margin: 0 }}>Optimized debt payoff</h3>
              <div className="seg">
                {(['avalanche', 'snowball'] as const).map((s) => (
                  <button key={s} className={d.payoff.strategy === s ? 'on' : ''} onClick={() => setPayoff(s)}>{s[0].toUpperCase() + s.slice(1)}</button>
                ))}
                <button className={d.payoff.strategy === 'custom' || editingOrder ? 'on' : ''} onClick={toggleOrder}>Custom</button>
              </div>
            </div>
            {editingOrder && (
              <div style={{ marginBottom: 12 }}>
                <div className="eyebrow">Pay off in this order</div>
                {order.map((name, k) => (
                  <div className="row" key={name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '4px 0' }}>
                    <span>{k + 1}. {name}</span>
                    <span>
                      <button className="btn small" disabled={k === 0} onClick={() => move(k, -1)} aria-label={`Move ${name} up`}>↑</button>{' '}
                      <button className="btn small" disabled={k === order.length - 1} onClick={() => move(k, 1)} aria-label={`Move ${name} down`}>↓</button>
                    </span>
                  </div>
                ))}
                <button className="btn small primary" onClick={applyOrder}>Apply order</button>
              </div>
            )}
            {d.payoff.accounts.map((a) => {
              const end = a.paid_off ? new Date(a.paid_off).getTime() : longest
              return (
                <div className="payoff-row" key={a.name}>
                  <span>{a.name} · {pct(a.apr, a.apr * 100 % 1 ? 1 : 0)}</span>
                  <div className="track"><div style={{ width: `${Math.max(4, ((end - start) / (longest - start)) * 100)}%` }} /></div>
                  <span className="mono" style={{ textAlign: 'right' }}>Paid {monthYear(a.paid_off)}</span>
                </div>
              )
            })}
            <div className="payoff-foot">
              <div><div className="eyebrow">Debt-free</div><div className="big">{d.payoff.feasible ? monthYear(d.payoff.debt_free) : 'Never'}</div></div>
              <div><div className="eyebrow">Interest saved</div><div className="big" style={{ color: 'var(--green)' }}>{usd(d.payoff.interest_saved)}</div></div>
              <div>
                <div className="eyebrow">Extra payment / month: {usd(extra)}</div>
                <input type="range" min={0} max={3000} step={50} value={extra}
                  onChange={(e) => { const v = Number(e.target.value); setExtra(v); setPayoff(d.payoff.strategy, v) }} />
              </div>
            </div>
          </div>
        </div>

        <div className="insights" id="insights">
          {d.caps.length > 0 && (
            <div className="card">
              <h3>Budget Caps</h3>
              {d.caps.map((c) => (
                <div className="payoff-row" key={c.category}>
                  <span>{c.category}</span>
                  <div className="track"><div style={{ width: `${Math.min(100, (c.last_30_days / c.monthly_cap) * 100)}%`, background: c.over ? 'var(--orange)' : undefined }} /></div>
                  <span className="mono" style={{ textAlign: 'right', color: c.over ? 'var(--orange)' : undefined }}>{usd(c.last_30_days)} / {usd(c.monthly_cap)}</span>
                </div>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h2>Agent insights</h2>
            <button className="btn small" onClick={refresh} disabled={busy}>{busy ? <><span className="spinner" /> Agents working</> : 'Refresh'}</button>
          </div>
          {err && <p className="error">{err}</p>}
          {insights.length === 0 && !busy && <div className="card insight muted">No insights yet. Press Refresh to have the agents analyse your Documents.</div>}
          {insights.map((i) => (
            <div className="card insight" key={i.id}>
              <div className="top"><span className={`tag ${AGENT_CLASS[i.agent]}`}>{i.agent}</span><span>{ago(i.created_at)}</span></div>
              <h4>{i.title}</h4>
              <p>{i.body}</p>
              {i.sources.length > 0 && <div className="src">Citations: {i.sources.map((s, k) => <span key={k}>{s.document} · {s.rows}{k < i.sources.length - 1 ? ', ' : ''}</span>)}</div>}
              {why === i.id && (
                <div className="src">
                  <div>Tool calls: {i.evidence.length ? i.evidence.map((c) => `${c.tool}${c.rows == null ? '' : ` (${c.rows} rows)`}`).join(', ') : 'not recorded'}</div>
                </div>
              )}
              <div className="actions">
                <button className="btn small" onClick={() => setWhy(why === i.id ? null : i.id)}>Why?</button>
                {i.status === 'in_plan' ? <span className="in-plan">✓ In Plan</span> : (
                  <>
                    <button className="btn small" onClick={() => act(i, 'dismiss')}>Dismiss</button>
                    <button className="btn small primary" onClick={() => act(i, 'add_to_plan')}>
                      {i.action_kind === 'set_cap' && i.action_payload.amount ? 'Set cap' : 'Add to plan'}
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
          <div className="upcoming">
            <div className="eyebrow">Upcoming</div>
            {d.upcoming.map((u) => (
              <div className="row" key={u.label}><span>{u.label}</span><span className="mono">{new Date(u.date + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: '2-digit' })}</span></div>
            ))}
          </div>
        </div>
      </div>
    </>
  )
}
