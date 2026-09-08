import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'

type LaneStatus = {
  enabled?: boolean
  instruments?: string[]
  today?: { closed?: number; net_rupees?: number; wins?: number }
  all_time?: { closed?: number; net_rupees?: number }
  open_positions?: Record<string, unknown>
}
type CryptoStatus = {
  lanes?: Record<string, boolean>
  trading_mode?: string
  symbols?: string[]
}

export type LiveStatus = {
  enabled: boolean
  mode: string
  instruments: string[]
  today?: { closed?: number; net_rupees?: number }
  allTime?: { closed?: number; net_rupees?: number }
  open: number
}

/** Live enable / mode / today's P&L per strategy, keyed by `StrategyDef.statusKey`. */
export function useStrategyStatus(): Record<string, LiveStatus> {
  const poll = usePollMs(30_000)
  const futures = useQuery({ queryKey: ['futures', 'status'], queryFn: () => api<LaneStatus>('/api/futures/status'), refetchInterval: poll })
  const optionsCpr = useQuery({ queryKey: ['options-cpr', 'status'], queryFn: () => api<LaneStatus>('/api/options-cpr/status'), refetchInterval: poll })
  const crypto = useQuery({ queryKey: ['crypto', 'status'], queryFn: () => api<CryptoStatus>('/api/crypto/status'), refetchInterval: poll })

  const lane = (d: LaneStatus | undefined): LiveStatus => ({
    enabled: !!d?.enabled,
    mode: 'PAPER',
    instruments: d?.instruments ?? ['NIFTY', 'BANKNIFTY', 'SENSEX'],
    today: d?.today,
    allTime: d?.all_time,
    open: Object.keys(d?.open_positions ?? {}).length,
  })

  const cd = crypto.data
  const cLane = (key: string): LiveStatus => ({
    enabled: !!cd?.lanes?.[key],
    mode: cd?.trading_mode ?? 'PAPER',
    instruments: cd?.symbols ?? ['BTCUSD', 'ETHUSD'],
    open: 0,
  })

  return {
    futures: lane(futures.data),
    options_cpr: lane(optionsCpr.data),
    ny_n_break: cLane('ny_n_break'),
    ichimoku: cLane('ichimoku'),
    fvg_scalp: cLane('fvg_scalp'),
  }
}
