import { useMemo } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from './usePageVisible'
import type { LogRow, TradeRow } from '../types/analytics'

/** Shared shape behind useCryptoJournal/useFuturesJournal/useCommoditiesJournal:
 *  fetch `{trades: T[]}` from `url`, map into the index dashboard's row shapes.
 *  `enabled` is false when this source isn't in view, so the poll is idle. */
export function useJournal<T>(
  queryKey: string,
  url: string,
  pollMs: number,
  enabled: boolean,
  toLogRows: (rows: T[]) => LogRow[],
  toTradeRows: (rows: T[]) => TradeRow[],
) {
  const poll = usePollMs(pollMs, enabled)
  const q = useQuery({
    queryKey: [queryKey],
    queryFn: () => api<{ trades: T[] }>(url),
    refetchInterval: poll,
    enabled,
    placeholderData: keepPreviousData,
  })

  const raw = q.data?.trades
  return useMemo(
    () => ({
      trades: raw ? toTradeRows(raw) : [],
      logRows: raw ? toLogRows(raw) : [],
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- callers pass stable
    // module-level mappers (or a fresh but pure per-row map); only `raw` changing
    // should trigger recompute, matching the three hooks this replaces.
    [raw],
  )
}
