import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { STRATEGIES } from '../lib/strategies'
import { usePollMs } from '../hooks/usePageVisible'

type LivePairRow = {
  strategy: string
  coin: string
  trades: number | null
  net_usd: number | null
  eligible: boolean
  live: boolean
  reason: string | null
}

type LivePairs = {
  armed: boolean
  read_ok: boolean
  pairs: LivePairRow[]
}

const stratName = (id: string) =>
  STRATEGIES.find((x) => x.id === id)?.name ?? id.replace(/_/g, ' ')

/** Which crypto (strategy, coin) pairs use real money. The server decides; this only renders it. */
export function CryptoLivePairs() {
  const poll = usePollMs(60_000)
  // key sits under ['crypto'] so arm / disarm / mode changes refresh it at once
  const q = useQuery({
    queryKey: ['crypto', 'live-pairs'],
    queryFn: () => api<LivePairs>('/api/crypto/live-pairs'),
    refetchInterval: poll,
    placeholderData: keepPreviousData,
  })
  const d = q.data

  return (
    <div className="mt-3 border-t border-[var(--hair)] pt-3">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
        {d?.armed ? 'Right now: which pairs use real money' : 'If you arm: which pairs would use real money'}
      </p>
      {q.isError && !d ? (
        <p className="mt-1 text-[12.5px] text-slate-500">
          Live/paper list not available yet — the app may need a restart.
        </p>
      ) : !d ? (
        <p className="mt-1 text-[12.5px] text-slate-500">Loading…</p>
      ) : d.pairs.length === 0 ? (
        <p className="mt-1 text-[12.5px] text-slate-500">No crypto strategies are switched on.</p>
      ) : (
        <ul className="mt-2 space-y-1">
          {d.pairs.map((p) => (
            <li key={`${p.strategy}|${p.coin}`} className="text-[12.5px]">
              <span className="font-semibold text-slate-200">{stratName(p.strategy)}</span>{' '}
              <span
                className={
                  p.live
                    ? 'rounded bg-[var(--armed)]/15 px-1.5 py-0.5 font-mono text-[10px] uppercase text-[var(--armed)]'
                    : 'rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10px] uppercase text-slate-400'
                }
              >
                {p.coin.replace(/USD.?$/, '')} {p.live ? 'LIVE' : 'PAPER'}
              </span>{' '}
              {p.reason ? <span className="text-[11.5px] text-slate-500">{p.reason}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
