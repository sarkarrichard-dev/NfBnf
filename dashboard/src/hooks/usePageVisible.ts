import { useEffect, useState } from 'react'

/** True when the browser tab is visible (pause polls when hidden). */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(
    () => typeof document === 'undefined' || document.visibilityState === 'visible',
  )

  useEffect(() => {
    const onChange = () => setVisible(document.visibilityState === 'visible')
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])

  return visible
}

/** Poll interval in ms when page is visible; false when hidden. */
export function usePollMs(intervalMs: number, enabled = true): number | false {
  const visible = usePageVisible()
  if (!enabled || !visible) return false
  return intervalMs
}
