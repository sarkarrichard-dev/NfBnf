import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'

export type PerfRow = {
  venue: 'india' | 'crypto'
  strategy: string
  instrument: string
  mode: string
  currency: 'INR' | 'USD'
  trades: number
  wins: number
  losses: number
  win_rate: number | null
  gross: number
  charges: number
  slippage: number
  net: number
  avg_win: number
  avg_loss: number
  expectancy: number
  best: number
  worst: number
  priced_pct: number | null
  first_day: string | null
  last_day: string | null
}

export type PerfTotals = {
  currency: 'INR' | 'USD'
  strategies: number
  instruments: number
  trades: number
  win_rate: number | null
  gross: number
  charges: number
  slippage: number
  net: number
}

export type StrategyPerformance = {
  generated_at: string
  india: { rows: PerfRow[]; totals: PerfTotals }
  crypto: { rows: PerfRow[]; totals: PerfTotals }
  note: string
}

/** Per-(strategy, instrument) scorecard from the live journals. */
export function useStrategyPerformance(enabled: boolean) {
  const poll = usePollMs(30_000, enabled)
  return useQuery({
    queryKey: ['strategy-performance'],
    queryFn: () => api<StrategyPerformance>('/api/strategy-performance'),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })
}
