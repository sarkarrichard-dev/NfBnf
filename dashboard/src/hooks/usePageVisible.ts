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

/** Poll interval in ms. This is a trading dashboard — Richard, 2026-09-16:
 *  "we are dealing in finance which relies on live data reflection so we
 *  need to always keep this open no matter its in the background or
 *  foreground." Positions and prices must keep updating even when the tab
 *  isn't focused, so this deliberately ignores page visibility. */
export function usePollMs(intervalMs: number, enabled = true): number | false {
  return enabled ? intervalMs : false
}
