import { useCallback } from 'react'

// ponytail: in-memory only — history resets on page reload / tab close.
// Add a backend intraday endpoint (e.g. /api/analytics/intraday) if the
// sparklines need to survive a refresh.
const MAX_POINTS = 120
// A long-running session accumulates a series key per (trade, stat) ever seen,
// never just the ones currently on screen — cap the key count too (LRU via
// Map's insertion order) so this doesn't grow without bound.
const MAX_KEYS = 500
const store = new Map<string, number[]>()

/** Append `value` to the series `key` (dedupes identical trailing values). */
export function pushPoint(key: string, value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return
  const arr = store.get(key) ?? []
  if (arr.length && arr[arr.length - 1] === value) return
  arr.push(value)
  if (arr.length > MAX_POINTS) arr.shift()
  store.delete(key) // re-insert to mark most-recently-used
  store.set(key, arr)
  if (store.size > MAX_KEYS) {
    const oldest = store.keys().next().value
    if (oldest !== undefined) store.delete(oldest)
  }
}

export function getSeries(key: string): number[] {
  const arr = store.get(key)
  if (arr === undefined) return []
  // reading counts as use too, or a flat/closed series with no new pushes
  // would look "least recently used" and be the first evicted while still on screen
  store.delete(key)
  store.set(key, arr)
  return arr
}

/** Stable push/get pair for a component. */
export function useSeries() {
  return {
    push: useCallback(pushPoint, []),
    get: useCallback(getSeries, []),
  }
}
