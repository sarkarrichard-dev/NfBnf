import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'

export type LearnRow = {
  venue: 'india' | 'crypto'
  strategy: string
  instrument: string
  trades: number
  trading_days: number
  state: 'watching' | 'observing' | 'ready'
  frozen: boolean
  win_rate: number | null
  net: number
  observations: string[]
  next_step: string
}

export type StrategyLearning = {
  generated_at: string
  epoch: string | null
  india: LearnRow[]
  crypto: LearnRow[]
  ladder: Record<string, string>
}

export function useStrategyLearning(enabled: boolean) {
  const poll = usePollMs(60_000, enabled)
  return useQuery({
    queryKey: ['strategy-learning'],
    queryFn: () => api<StrategyLearning>('/api/strategy-learning'),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })
}
