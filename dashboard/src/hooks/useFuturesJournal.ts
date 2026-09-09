import { useMemo } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'
import {
  futuresToLogRows,
  futuresToTradeRows,
  type FuturesJournalRow,
} from '../lib/futuresRows'

/** Closed futures paper trades (index + stock), mapped into the index row shapes.
 *  `enabled` is false when only other sources are in view, so the poll is idle. */
export function useFuturesJournal(enabled: boolean) {
  const poll = usePollMs(20_000, enabled)
  const q = useQuery({
    queryKey: ['futures-journal'],
    queryFn: () => api<{ trades: FuturesJournalRow[] }>('/api/futures/journal?limit=1000'),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })

  const raw = q.data?.trades
  return useMemo(
    () => ({
      trades: raw ? futuresToTradeRows(raw) : [],
      logRows: raw ? futuresToLogRows(raw) : [],
    }),
    [raw],
  )
}
