/** Largest preview the backend returns (GET /api/documents/{id}?limit=). */
export const MAX_PREVIEW = 1000

export function greetingFor(hour: number, user?: string): string {
  const base = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
  const first = user?.trim().split(' ')[0]
  return first ? `${base}, ${first}` : base
}

/** Label for the "load more rows" button, or null when nothing is left to load. */
export function showAllLabel(shown: number, total: number): string | null {
  if (shown >= Math.min(total, MAX_PREVIEW)) return null
  return total > MAX_PREVIEW ? `Show first ${MAX_PREVIEW} rows` : `Show all ${total} rows`
}
