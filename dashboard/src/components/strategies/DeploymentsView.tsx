import { cn } from '../../lib/cn'
import { STRATEGIES } from '../../lib/strategies'
import { useStrategyStatus } from '../../hooks/useStrategyStatus'

const rupee = (v?: number | null) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}₹${Math.abs(Math.round(v)).toLocaleString('en-IN')}`
const pnlClass = (v?: number | null) =>
  v == null || v === 0 ? 'text-slate-300' : v > 0 ? 'text-[var(--up)]' : 'text-[var(--down)]'

/** Deployments — what is actually running: strategy × lane × mode, with
 *  today's paper P&L per lane. One row per catalog strategy. */
export function DeploymentsView() {
  const status = useStrategyStatus()
  const running = STRATEGIES.filter((s) => status[s.statusKey]?.enabled)

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        {running.length} of {STRATEGIES.length} strategies deployed.
        {' '}All lanes are paper today — live arming is per-exchange, on the Exchanges tab.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[40rem] text-xs">
          <thead>
            <tr className="border-b border-[var(--hair)] text-left font-mono text-[10.5px] uppercase tracking-wide text-slate-500">
              <th className="py-2 pr-3 font-medium">Strategy</th>
              <th className="py-2 pr-3 font-medium">Instrument</th>
              <th className="py-2 pr-3 font-medium">Mode</th>
              <th className="py-2 pr-3 font-medium">State</th>
              <th className="py-2 pr-3 text-right font-medium">Today</th>
              <th className="py-2 pr-3 text-right font-medium">All time</th>
              <th className="py-2 text-right font-medium">Open</th>
            </tr>
          </thead>
          <tbody>
            {STRATEGIES.map((s) => {
              const live = status[s.statusKey]
              const on = !!live?.enabled
              return (
                <tr key={s.id} className="border-b border-[var(--hair-soft)]">
                  <td className="py-2 pr-3 font-semibold text-slate-100">{s.name}</td>
                  <td className="py-2 pr-3 text-slate-400">{s.instrument}</td>
                  <td className="py-2 pr-3 font-mono text-slate-300">{live?.mode ?? 'PAPER'}</td>
                  <td className="py-2 pr-3">
                    <span
                      className={cn(
                        'rounded-full px-2 py-0.5 text-[10px] font-semibold',
                        on ? 'bg-[var(--up)]/15 text-[var(--up)]' : 'bg-white/10 text-slate-400',
                      )}
                    >
                      {on ? 'running' : 'dormant'}
                    </span>
                  </td>
                  <td className={cn('py-2 pr-3 text-right font-mono tabular-nums', pnlClass(live?.today?.net_rupees))}>
                    {live?.today ? rupee(live.today.net_rupees) : '—'}
                  </td>
                  <td className={cn('py-2 pr-3 text-right font-mono tabular-nums', pnlClass(live?.allTime?.net_rupees))}>
                    {live?.allTime ? rupee(live.allTime.net_rupees) : '—'}
                  </td>
                  <td className="py-2 text-right font-mono tabular-nums text-slate-300">{live?.open ?? 0}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="text-[11px] text-slate-600">
        Crypto per-lane P&L is on the Crypto tab's day review; the index lanes report their own
        today / all-time buckets here.
      </p>
    </div>
  )
}
