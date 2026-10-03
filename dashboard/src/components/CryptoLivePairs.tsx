import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { pnlCls, usd } from '../lib/cryptoFmt'
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

const coinName = (coin: string) => coin.replace(/USD.?$/, '')

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

  // group by strategy, in the order the server sent them
  const groups: { id: string; rows: LivePairRow[] }[] = []
  for (const p of d?.pairs ?? []) {
    const g = groups.find((x) => x.id === p.strategy)
    if (g) g.rows.push(p)
    else groups.push({ id: p.strategy, rows: [p] })
  }
  const liveCount = d?.pairs.filter((p) => p.live).length ?? 0
  const eligibleCount = d?.pairs.filter((p) => p.eligible).length ?? 0

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
        <>
          <p className="mt-1 font-mono text-[11px] tabular-nums text-slate-500">
            {d.armed
              ? `${liveCount} of ${d.pairs.length} pairs use real money`
              : `${eligibleCount} of ${d.pairs.length} pairs would use real money once armed`}
          </p>
          <p className="mt-0.5 text-[11.5px] text-slate-500">
            {d.armed
              ? 'New trades on LIVE pairs use real money; PAPER pairs keep practising. Trades already open keep the mode they opened with.'
              : 'Nothing here uses real money until crypto is armed.'}
          </p>
          {!d.read_ok ? (
            <p className="mt-0.5 text-[11.5px] text-[var(--warn)]">
              Could not read the trade results just now, so every pair stays on paper.
            </p>
          ) : null}
          <ul className="mt-2 space-y-2">
            {groups.map((g) => {
              const paper = g.rows.filter((p) => !p.live)
              const sameReason = paper.length > 0 && paper.every((p) => p.reason === paper[0].reason)
              return (
                <li key={g.id} className="text-[12.5px]">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-semibold text-slate-200">{stratName(g.id)}</span>
                    {g.rows.map((p) => (
                      <span
                        key={p.coin}
                        className={
                          p.live
                            ? 'rounded bg-[var(--armed)]/15 px-1.5 py-0.5 font-mono text-[10px] uppercase text-[var(--armed)]'
                            : 'rounded bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10px] uppercase text-slate-400'
                        }
                      >
                        {coinName(p.coin)} {p.live ? 'LIVE' : 'PAPER'}
                      </span>
                    ))}
                  </div>
                  {sameReason ? (
                    <p className="pl-3 text-[11.5px] text-slate-500">{paper[0].reason}</p>
                  ) : (
                    paper.map((p) => (
                      <p key={p.coin} className="pl-3 text-[11.5px] text-slate-500">
                        <span className="font-mono text-slate-300">{coinName(p.coin)}</span> {p.reason}
                        {p.trades ? (
                          <span className="font-mono tabular-nums">
                            {' '}
                            · {p.trades} {p.trades === 1 ? 'trade' : 'trades'} ·{' '}
                            <span className={pnlCls(p.net_usd)}>{usd(p.net_usd)}</span>
                          </span>
                        ) : null}
                      </p>
                    ))
                  )}
                </li>
              )
            })}
          </ul>
        </>
      )}
    </div>
  )
}
