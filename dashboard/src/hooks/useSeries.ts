import { useCallback } from 'react'

// ponytail: in-memory only — history resets on page reload / tab close.
// Add a backend intraday endpoint (e.g. /api/analytics/intraday) if the
// sparklines need to survive a refresh.
const MAX_POINTS = 120
const store = new Map<string, number[]>()

/** Append `value` to the series `key` (dedupes identical trailing values). */
export function pushPoint(key: string, value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return
  const arr = store.get(key) ?? []
  if (arr.length && arr[arr.length - 1] === value) return
  arr.push(value)
  if (arr.length > MAX_POINTS) arr.shift()
  store.set(key, arr)
}

export function getSeries(key: string): number[] {
  return store.get(key) ?? []
}

/** Stable push/get pair for a component. */
export function useSeries() {
  return {
    push: useCallback(pushPoint, []),
    get: useCallback(getSeries, []),
  }
}
