import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AGENT_CLASS, api, askAdvisor, monthYear, usd, type PayoffComparison, type TraceEvent } from '../api'

type Answer = Extract<TraceEvent, { type: 'answer' }>
type Step = { agent: string; seconds?: number; tools: { tool: string; rows: number | null }[] }

interface Turn {
  question: string
  intent?: string
  steps: Step[]
  answer?: Answer
  error?: string
  started: number
  total?: number
  applied?: string
  comparison?: PayoffComparison
}

const QUICK = ['Analyze my debt', 'Make a budget', 'Savings goal']
const QUICK_Q: Record<string, string> = {
  'Analyze my debt': 'Analyze my debt: what does each account cost me, and what should I tackle first?',
  'Make a budget': 'Make me a monthly budget with caps for the categories I overspend on.',
  'Savings goal': 'How do I build a 6-month emergency fund, and by when can I get there?',
}
const COLOR: Record<string, string> = {
  'Debt Analyzer': 'var(--orange)', 'Budget Coach': 'var(--green)', 'Savings Strategist': 'var(--blue)', 'Payoff Optimizer': 'var(--purple)',
}

function stepSummary(s: Step): string {
  return s.tools.map((t) => `${t.tool}()${t.rows != null ? ` → ${t.rows} rows` : ''}`).join(' · ') || 'reasoning'
}

export default function Advisor({ initial, onConsumed, onAnswered }: { initial: string | null; onConsumed: () => void; onAnswered?: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [showSteps, setShowSteps] = useState(true)
  const [capsSet, setCapsSet] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)

  const update = (fn: (t: Turn) => Turn) => setTurns((ts) => [...ts.slice(0, -1), fn(ts[ts.length - 1])])

  const ask = async (question: string) => {
    if (!question.trim() || busy) return
    setBusy(true); setInput(''); setCapsSet(false)
    setTurns((ts) => [...ts, { question, steps: [], started: Date.now() }])
    try {
      const history = turns.filter((t) => t.answer).map((t) => ({ question: t.question, answer: t.answer!.markdown }))
      for await (const ev of askAdvisor(question, history)) {
        if (ev.type === 'route') update((t) => ({ ...t, intent: ev.intent, steps: ev.agents.map((agent) => ({ agent, tools: [] })) }))
        if (ev.type === 'tool') update((t) => ({ ...t, steps: t.steps.map((s) => (s.agent === ev.agent ? { ...s, tools: [...s.tools, { tool: ev.tool, rows: ev.rows }] } : s)) }))
        if (ev.type === 'agent_done') update((t) => ({ ...t, steps: t.steps.map((s) => (s.agent === ev.agent ? { ...s, seconds: ev.seconds } : s)) }))
        if (ev.type === 'answer') update((t) => ({ ...t, answer: ev, total: (Date.now() - t.started) / 1000 }))
        if (ev.type === 'error') update((t) => ({ ...t, error: ev.message }))
        if (ev.type === 'end') onAnswered?.()
      }
    } catch (e) {
      update((t) => ({ ...t, error: (e as Error).message }))
    } finally {
      setBusy(false)
    }
  }

  const started = useRef<string | null>(null)
  useEffect(() => {
    if (initial && started.current !== initial) {
      started.current = initial // React runs effects twice in development; ask only once
      ask(initial)
      onConsumed()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial])
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  const last = turns[turns.length - 1]

  const applyPlan = async (a: Answer) => {
    if (!a.payoff) return
    const strategy = a.payoff.strategy ?? 'avalanche'
    await api.setPayoff(strategy, a.payoff.extra_monthly, a.payoff.custom_order)
    update((t) => ({ ...t, applied: `${strategy} with ${usd(a.payoff!.extra_monthly)}/month extra is now on your dashboard` }))
  }

  const compare = async (a: Answer) => {
    if (!a.payoff) return
    try {
      const comparison = await api.comparePayoff(a.payoff.extra_monthly)
      update((t) => ({ ...t, comparison }))
    } catch (e) {
      update((t) => ({ ...t, error: (e as Error).message }))
    }
  }

  const setCaps = async (a: Answer) => {
    await api.setCaps(a.cuts.map((c) => ({ category: c.category, monthly_cap: c.proposed_cap })))
    setCapsSet(true)
  }

  return (
    <div className="advisor">
      <div>
        <button className="btn primary" style={{ width: '100%', padding: '12px' }} onClick={() => { setTurns([]); setInput('') }}>+ New question</button>
        <div className="recent">
          <h4>RECENT</h4>
          {turns.length === 0 && <div className="muted" style={{ fontSize: 13, padding: '0 10px' }}>Nothing yet</div>}
          {[...turns].reverse().map((t, i) => <div key={i} className={`recent-item ${t === last ? 'on' : ''}`}>{t.question}</div>)}
        </div>
        <div className="quick">
          <h4>QUICK PROMPTS</h4>
          <div className="chips">{QUICK.map((p) => <button key={p} className="chip" onClick={() => ask(QUICK_Q[p])} disabled={busy}>{p}</button>)}</div>
        </div>
      </div>

      <div>
        <div className="thread">
          {turns.length === 0 && (
            <div className="card">
              <h3 className="serif" style={{ fontWeight: 500, fontSize: 24 }}>Ask your financial team</h3>
              <p className="muted">The Orchestrator routes your question to the right specialists. Every number comes from your Documents and links back to its rows.</p>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className="thread">
              <div className="bubble-user">{t.question}</div>
              {t.steps.length > 0 && (
                <div className="route-card">
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <b>Route · {t.steps.length} agent{t.steps.length > 1 ? 's' : ''}{t.total ? ` · ${t.total.toFixed(1)} s` : ''}</b>
                    <button className="btn small" style={{ border: 0, background: 'none', color: 'var(--green)' }} onClick={() => setShowSteps(!showSteps)}>{showSteps ? 'Hide steps' : 'Show steps'}</button>
                  </div>
                  {showSteps && (
                    <ol>
                      {t.steps.map((s) => (
                        <li key={s.agent}>
                          <span className={`tag ${AGENT_CLASS[s.agent]}`}>{s.agent}</span>{' '}
                          <span className="muted">{s.seconds == null && !t.answer && !t.error ? <><span className="spinner" /> working…</> : stepSummary(s)}</span>
                        </li>
                      ))}
                    </ol>
                  )}
                </div>
              )}
              {!t.answer && !t.error && t.steps.length === 0 && <div className="muted"><span className="spinner" /> Orchestrator is routing your question…</div>}
              {t.error && <div className="card error">{t.error}</div>}
              {t.answer && (
                <div className="card answer">
                  <Markdown remarkPlugins={[remarkGfm]}>{t.answer.markdown}</Markdown>
                  {t.answer.sources.length > 0 && (
                    <div className="cites">{t.answer.sources.map((s, k) => <span className="cite" key={k} title={`[${k + 1}] ${s.document} · ${s.rows}${s.pages ? ` (${s.pages})` : ''}`}>[{k + 1}] {s.document} · {s.rows}{s.pages ? ` (${s.pages})` : ''}</span>)}</div>
                  )}
                  <div className="row-actions">
                    {t.answer.payoff && <button className="btn primary" onClick={() => applyPlan(t.answer!)} disabled={!!t.applied}>Apply Payoff Strategy to dashboard</button>}
                    {t.answer.payoff && <button className="btn" onClick={() => compare(t.answer!)} disabled={busy || !!t.comparison}>
                      Compare with {t.answer.payoff.strategy === 'snowball' ? 'avalanche' : 'snowball'}</button>}
                    <button className="btn" onClick={() => window.print()}>Export PDF</button>
                  </div>
                  {t.applied && <p className="in-plan" style={{ marginTop: 10 }}>✓ {t.applied}</p>}
                  {t.comparison && t.answer.payoff && (
                    <table style={{ marginTop: 12 }}>
                      <caption className="muted" style={{ textAlign: 'left' }}>With {usd(t.answer.payoff.extra_monthly)}/month extra</caption>
                      <thead><tr><th></th><th>Avalanche</th><th>Snowball</th></tr></thead>
                      <tbody>
                        <tr><td>Debt-free</td>{(['avalanche', 'snowball'] as const).map((s) => <td key={s} className="mono">{monthYear(t.comparison![s].debt_free)}</td>)}</tr>
                        <tr><td>Total interest</td>{(['avalanche', 'snowball'] as const).map((s) => <td key={s} className="mono">{usd(t.comparison![s].total_interest)}</td>)}</tr>
                        <tr><td>Months</td>{(['avalanche', 'snowball'] as const).map((s) => <td key={s} className="mono">{t.comparison![s].months ?? '—'}</td>)}</tr>
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          ))}
          <div ref={bottom} />
        </div>
        <form className="composer" onSubmit={(e) => { e.preventDefault(); ask(input) }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder={turns.length ? 'Ask a follow-up…' : 'Ask about your debt, budget or savings…'} />
          <button className="btn primary" style={{ padding: '0 22px' }} disabled={busy}>Send</button>
        </form>
      </div>

      <div className="stack">
        <div className="card">
          <h3>Agent trace</h3>
          {!last ? <p className="muted" style={{ fontSize: 14 }}>Ask a question to see which agents run.</p> : (
            <div className="trace">
              <div className="trace-step" style={{ '--c': 'var(--ink)' } as React.CSSProperties}>
                <b>Orchestrator</b>
                <div className="muted">{last.intent ? `Intent: ${last.intent} · routed to ${last.steps.length} agents` : 'Routing…'}</div>
              </div>
              {last.steps.map((s) => (
                <div className="trace-step" key={s.agent} style={{ '--c': COLOR[s.agent] } as React.CSSProperties}>
                  <b>{s.agent}</b> {s.seconds != null && <span className="mono muted">{s.seconds} s</span>}
                  <div className="muted">{s.tools.length ? `Tool: ${stepSummary(s)}` : s.seconds != null ? 'No tools needed' : 'Waiting…'}</div>
                </div>
              ))}
            </div>
          )}
        </div>
        {last?.answer && last.answer.cuts.length > 0 && (
          <div className="card data-used">
            <h3>Data used</h3>
            <table>
              <thead><tr><th>Category</th><th>30 days</th><th>Cap</th></tr></thead>
              <tbody>{last.answer.cuts.map((c) => (
                <tr key={c.category}><td>{c.category}</td><td className="mono">{c.last_30_days.toLocaleString('en-US', { maximumFractionDigits: 0 })}</td><td className="mono">{c.proposed_cap.toLocaleString('en-US', { maximumFractionDigits: 0 })}</td></tr>
              ))}</tbody>
            </table>
            <button className="btn small primary" style={{ marginTop: 12 }} onClick={() => setCaps(last.answer!)} disabled={capsSet}>{capsSet ? '✓ Budget Caps set' : 'Set these Budget Caps'}</button>
          </div>
        )}
        <p className="disclaimer">Guidance only, not regulated financial advice. Every number links back to the row it came from.</p>
      </div>
    </div>
  )
}
