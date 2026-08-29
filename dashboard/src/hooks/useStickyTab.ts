import { useEffect, useState } from 'react'

/** Tab selection that survives a refresh, so reloading mid-session returns to
 *  the view you were watching. Degrades silently in private mode. */
export function useStickyTab(key: string, fallback: string): [string, (v: string) => void] {
  const [tab, setTab] = useState<string>(() => {
    try {
      return localStorage.getItem(key) || fallback
    } catch {
      return fallback
    }
  })
  useEffect(() => {
    try {
      localStorage.setItem(key, tab)
    } catch {
      /* private mode — the tab simply won't persist */
    }
  }, [key, tab])
  return [tab, setTab]
}
