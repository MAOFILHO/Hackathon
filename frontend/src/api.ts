// Typed client for the FinAgent backend. Shapes mirror backend/finagent/schemas.py and api.py.

export type Flow = 'income' | 'spending' | 'transfer' | 'debt_service'
export type Strategy = 'avalanche' | 'snowball' | 'custom'
export type AgentName = 'Debt Analyzer' | 'Payoff Optimizer' | 'Budget Coach' | 'Savings Strategist'
export type DocType = 'spending' | 'debt' | 'income' | 'receipt'

export interface Transaction {
  row_no: number
  date: string
  description: string
  amount: number
  page: number | null
  category: string
  confidence: number
  flow: Flow
  needs_review: boolean
}

export interface DebtAccount {
  row_no: number
  name: string
  kind: string
  balance: number
  apr: number
  min_payment: number
  due_day: number | null
}

export interface Payslip { period: string; employer: string; gross: number; net: number }

export interface Doc {
  id: number
  name: string
  file_type: string
  doc_type: string
  status: 'extracting' | 'review' | 'ready' | 'failed'
  row_count: number
  created_at: string
  error?: string | null
}

export interface Mapping {
  date: string | null
  description: string | null
  amount: string | null
  debit: string | null
  credit: string | null
  doc_type: DocType
}

export interface DocPreview {
  document: Doc
  columns: string[]
  mapping?: Mapping
  transactions: Transaction[]
  debts: DebtAccount[]
  payslips: Payslip[]
  needs_review: number
  categories: string[]
}

export interface PayoffAccount { name: string; apr: number; balance: number; paid_off: string | null }

export interface Payoff {
  strategy: Strategy
  extra_monthly: number
  feasible: boolean
  debt_free: string | null
  months: number | null
  total_interest: number
  interest_saved: number
  accounts: PayoffAccount[]
}

export interface Dashboard {
  as_of: string
  month_label: string
  income: { value: number; note: string; payslip_net: number | null; matches_bank: boolean | null }
  spending_30d: { value: number; vs_3mo_avg: number | null }
  debt: { total: number; accounts: number; avg_apr: number }
  savings_rate: { value: number | null; target: number }
  cashflow: { month: string; income: number; spending: number; debt_service: number }[]
  caps: { category: string; monthly_cap: number; last_30_days: number; over: boolean }[]
  payoff: Payoff
  upcoming: { label: string; date: string }[]
}

export interface Source { document: string; rows: string; count: number; pages?: string | null }

export interface PayoffRun { strategy: string; months: number | null; debt_free: string | null; total_interest: number; feasible: boolean }
export interface PayoffComparison { avalanche: PayoffRun; snowball: PayoffRun }

export interface Insight {
  id: number
  agent: AgentName
  title: string
  body: string
  action_kind: 'add_to_plan' | 'set_cap' | 'auto_save'
  action_payload: { category: string | null; amount: number | null }
  sources: Source[]
  evidence: { tool: string; rows: number | null }[]
  status: 'new' | 'in_plan' | 'dismissed'
  created_at: string
}

export type TraceEvent =
  | { type: 'route'; intent: string; agents: AgentName[] }
  | { type: 'agent_start'; agent: AgentName }
  | { type: 'tool'; agent: AgentName; tool: string; args: Record<string, unknown>; rows: number | null }
  | { type: 'agent_done'; agent: AgentName; seconds: number; calls: { tool: string; rows: number | null }[] }
  | { type: 'answer'; markdown: string; cuts: { category: string; last_30_days: number; proposed_cap: number }[];
      sources: Source[]; payoff: { extra_monthly: number; strategy?: Strategy; custom_order?: string[] } | null }
  | { type: 'error'; message: string }
  | { type: 'end' }

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: init?.body && !(init.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

const post = <T,>(path: string, body?: unknown) =>
  call<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  health: () => call<{ llm: boolean }>('/health'),
  dashboard: () => call<Dashboard>('/dashboard'),
  agents: () => call<Record<string, string>>('/agents'),
  comparePayoff: (extra: number) => call<PayoffComparison>(`/payoff/compare?extra=${encodeURIComponent(extra)}`),
  insights: () => call<Insight[]>('/insights'),
  refreshInsights: () => post<Insight[]>('/insights/refresh'),
  actOnInsight: (id: number, action: 'add_to_plan' | 'dismiss') => post(`/insights/${id}`, { action }),
  setPayoff: (strategy: string, extra_monthly: number, order?: string[]) =>
    post<Payoff>('/payoff-strategy', { strategy, extra_monthly, order }),
  setCaps: (caps: { category: string; monthly_cap: number }[]) => post('/budget-caps', caps),
  documents: () => call<Doc[]>('/documents'),
  document: (id: number) => call<DocPreview>(`/documents/${id}`),
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return call<{ id: number }>('/documents', { method: 'POST', body: form })
  },
  reextract: (id: number, mapping: Mapping) => post<DocPreview>(`/documents/${id}/reextract`, mapping),
  confirm: (id: number) => post(`/documents/${id}/confirm`),
  fixRow: (id: number, row: number, category: string) =>
    call<Transaction>(`/documents/${id}/rows/${row}`, { method: 'PATCH', body: JSON.stringify({ category }) }),
  remove: (id: number) => call(`/documents/${id}`, { method: 'DELETE' }),
}

/** POST /api/chat and yield each server-sent trace event as it arrives. */
export async function* askAdvisor(question: string): AsyncGenerator<TraceEvent> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}))
    yield { type: 'error', message: body.detail ?? `${res.status} ${res.statusText}` }
    return
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const data = frame.split(/\r?\n/).filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n')
      if (data) yield JSON.parse(data) as TraceEvent
    }
  }
}

export const usd = (n: number, digits = 0) =>
  n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: digits, minimumFractionDigits: digits })

export const pct = (n: number | null | undefined, digits = 1) => (n == null ? '—' : `${(n * 100).toFixed(digits)}%`)

export const monthYear = (iso: string | null) =>
  iso ? new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', year: '2-digit' }).replace(' ', " '") : '—'

export const AGENT_CLASS: Record<string, string> = {
  'Debt Analyzer': 'tag-debt',
  'Budget Coach': 'tag-budget',
  'Savings Strategist': 'tag-savings',
  'Payoff Optimizer': 'tag-payoff',
  Orchestrator: 'tag-orch',
}
