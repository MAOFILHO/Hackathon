import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, askAdvisor, pct, usd, type TraceEvent } from './api'

const encoder = new TextEncoder()

/** A fetch Response whose body arrives as the given chunks, one read each. */
function sseResponse(chunks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

const mockFetch = (res: Response) => {
  const fetchMock = vi.fn().mockResolvedValue(res)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function collect(gen: AsyncGenerator<TraceEvent>): Promise<TraceEvent[]> {
  const out: TraceEvent[] = []
  for await (const ev of gen) out.push(ev)
  return out
}

afterEach(() => vi.unstubAllGlobals())

describe('askAdvisor', () => {
  it('parses each server-sent event frame in order', async () => {
    mockFetch(sseResponse([
      'data: {"type":"agent_start","agent":"Debt Analyzer"}\n\n',
      'data: {"type":"end"}\n\n',
    ]))
    expect(await collect(askAdvisor('q'))).toEqual([
      { type: 'agent_start', agent: 'Debt Analyzer' },
      { type: 'end' },
    ])
  })

  it('reassembles a frame split across two chunks', async () => {
    mockFetch(sseResponse(['data: {"type":"erro', 'r","message":"boom"}\n', '\ndata: {"type":"end"}\n\n']))
    expect(await collect(askAdvisor('q'))).toEqual([
      { type: 'error', message: 'boom' },
      { type: 'end' },
    ])
  })

  it('handles CRLF line endings', async () => {
    mockFetch(sseResponse([
      'data: {"type":"agent_start","agent":"Budget Coach"}\r\n\r\n',
      'data: {"type":"end"}\r\n\r\n',
    ]))
    expect(await collect(askAdvisor('q'))).toEqual([
      { type: 'agent_start', agent: 'Budget Coach' },
      { type: 'end' },
    ])
  })

  it('yields an error event with the backend detail for a non-OK response', async () => {
    mockFetch(new Response(JSON.stringify({ detail: 'No API key' }), { status: 503, statusText: 'Service Unavailable' }))
    expect(await collect(askAdvisor('q'))).toEqual([{ type: 'error', message: 'No API key' }])
  })

  it('falls back to the status line when a non-OK response has no detail', async () => {
    mockFetch(new Response('not json', { status: 500, statusText: 'Server Error' }))
    expect(await collect(askAdvisor('q'))).toEqual([{ type: 'error', message: '500 Server Error' }])
  })

  it('posts {question, history} as JSON to /api/chat', async () => {
    const fetchMock = mockFetch(sseResponse([]))
    const history = [{ question: 'earlier?', answer: 'earlier answer' }]
    await collect(askAdvisor('now?', history))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/chat')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({ question: 'now?', history })
  })
})

describe('usd', () => {
  it('formats whole dollars by default', () => {
    expect(usd(1234.5)).toBe('$1,235')
  })
  it('honours the digits argument', () => {
    expect(usd(1234.5, 2)).toBe('$1,234.50')
  })
})

describe('pct', () => {
  it('formats a ratio with one decimal by default', () => {
    expect(pct(0.1234)).toBe('12.3%')
  })
  it('honours the digits argument', () => {
    expect(pct(0.1234, 0)).toBe('12%')
  })
  it('shows a dash for null and undefined', () => {
    expect(pct(null)).toBe('—')
    expect(pct(undefined)).toBe('—')
  })
})

describe('api.document', () => {
  const okJson = () => mockFetch(new Response('{}', { status: 200 }))

  it('requests the default preview when no limit is given', async () => {
    const fetchMock = okJson()
    await api.document(7)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/documents/7')
  })

  it('passes the limit as a query parameter', async () => {
    const fetchMock = okJson()
    await api.document(7, 120)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/documents/7?limit=120')
  })
})
