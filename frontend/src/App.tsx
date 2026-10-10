import { useEffect, useState } from 'react'
import { api } from './api'
import Advisor from './pages/Advisor'
import Dashboard from './pages/Dashboard'
import Documents from './pages/Documents'

export type Page = 'dashboard' | 'advisor' | 'documents'

const icons = {
  dashboard: <path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" />,
  advisor: <path d="M4 12a8 8 0 1 1 3.5 6.6L4 20l1.2-3.4A8 8 0 0 1 4 12z" />,
  payoff: <path d="M3 17l6-6 4 4 8-8M15 7h6v6" />,
  budget: <path d="M12 3v18M16.5 7.5c0-1.7-2-3-4.5-3s-4.5 1.3-4.5 3 2 2.6 4.5 3 4.5 1.3 4.5 3-2 3-4.5 3-4.5-1.3-4.5-3" />,
  savings: <path d="M6 10V7a6 6 0 0 1 12 0v3M5 10h14v10H5z" />,
  sources: <path d="M7 3h7l5 5v13H7zM14 3v5h5" />,
}

const Icon = ({ name }: { name: keyof typeof icons }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
    {icons[name]}
  </svg>
)

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')
  const [question, setQuestion] = useState<string | null>(null)
  const [agents, setAgents] = useState<Record<string, string>>({})
  const [llm, setLlm] = useState(true)
  const [refreshKey, setRefreshKey] = useState(0)

  useEffect(() => {
    api.health().then((h) => setLlm(h.llm)).catch(() => setLlm(false))
    const tick = () => api.agents().then(setAgents).catch(() => {})
    tick()
    const id = setInterval(tick, 1500)
    return () => clearInterval(id)
  }, [])

  const go = (p: Page, anchor?: string) => {
    setPage(p)
    if (anchor) setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ behavior: 'smooth' }), 50)
  }

  const ask = (q: string) => {
    setQuestion(q)
    setPage('advisor')
  }

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">FinAgent</div>
        <nav className="nav">
          <button className={page === 'dashboard' ? 'active' : ''} onClick={() => go('dashboard')}><Icon name="dashboard" />Dashboard</button>
          <button className={page === 'advisor' ? 'active' : ''} onClick={() => go('advisor')}><Icon name="advisor" />Ask advisor</button>
          <button onClick={() => go('dashboard', 'payoff')}><Icon name="payoff" />Debt payoff</button>
          <button onClick={() => go('dashboard', 'insights')}><Icon name="budget" />Budget</button>
          <button onClick={() => go('dashboard', 'insights')}><Icon name="savings" />Savings goals</button>
          <button className={page === 'documents' ? 'active' : ''} onClick={() => go('documents')}><Icon name="sources" />Documents</button>
        </nav>
        <h4>AGENTS</h4>
        {Object.entries(agents).map(([name, status]) => (
          <div className="agent-row" key={name}>
            <span className={`dot ${status}`} />
            {name} · {status}
          </div>
        ))}
        <div className="side-foot">Guidance only, not regulated financial advice.</div>
      </aside>
      <main className="main">
        {!llm && (
          <div className="banner">
            Agents are offline: add <span className="mono">ANTHROPIC_API_KEY</span> to <span className="mono">backend/.env</span> and
            restart the backend. The dashboard and CSV/XLSX uploads still work.
          </div>
        )}
        {page === 'dashboard' && <Dashboard key={refreshKey} onAsk={ask} />}
        {page === 'advisor' && <Advisor initial={question} onConsumed={() => setQuestion(null)} onAnswered={() => setRefreshKey((k) => k + 1)} />}
        {page === 'documents' && <Documents />}
      </main>
    </div>
  )
}
