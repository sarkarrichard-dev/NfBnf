import { useMemo } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'
import {
  cryptoToLogRows,
  cryptoToTradeRows,
  type CryptoJournalRow,
} from '../lib/cryptoRows'

/** Closed Delta-perp trades, mapped into the index dashboard's row shapes.
 *  `enabled` is false when only index trades are in view, so the poll is idle. */
export function useCryptoJournal(enabled: boolean) {
  const poll = usePollMs(15_000, enabled)
  const q = useQuery({
    queryKey: ['crypto-journal'],
    queryFn: () => api<{ trades: CryptoJournalRow[] }>('/api/crypto/journal?limit=500'),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })

  const raw = q.data?.trades
  return useMemo(
    () => ({
      trades: raw ? cryptoToTradeRows(raw) : [],
      logRows: raw ? cryptoToLogRows(raw) : [],
    }),
    [raw],
  )
}
