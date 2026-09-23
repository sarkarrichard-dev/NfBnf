import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'

export type LabRow = {
  strategy: string
  lane: 'buy' | 'sell'
  description: string
  instrument: string
  trades: number
  trading_days: number
  win_rate: number | null
  gross: number
  charges: number
  net: number
  per_trade: number | null
  verdict: 'COLLECTING' | 'PASSING' | 'DROPPED'
}

export type StrategyLab = {
  sessions: number
  bar: { min_trades: number; min_days: number }
  rows: LabRow[]
}

export function useStrategyLab(enabled: boolean) {
  const poll = usePollMs(5 * 60_000, enabled)
  return useQuery({
    queryKey: ['strategy-lab'],
    queryFn: () => api<StrategyLab>('/api/strategy-lab'),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })
}
