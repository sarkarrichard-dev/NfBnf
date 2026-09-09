import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { usePollMs } from '../hooks/usePageVisible'

export type LaneStatus = {
  enabled?: boolean
  instruments?: string[]
  lanes?: string[]
  open_positions?: Record<string, Record<string, unknown>>
  today?: { closed?: number; net_rupees?: number; wins?: number }
  all_time?: { closed?: number; net_rupees?: number }
  recent_trades?: Record<string, unknown>[]
}

export const rupees = (v?: number | null) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}₹${Math.abs(Math.round(v)).toLocaleString('en-IN')}`

export const pnlClass = (v?: number | null) =>
  v == null || v === 0 ? 'text-slate-300' : v > 0 ? 'text-emerald-300' : 'text-rose-300'

export function LaneCard({ title, data }: { title: string; data?: LaneStatus }) {
  const today = data?.today ?? {}
  const all = data?.all_time ?? {}
  const open = Object.entries(data?.open_positions ?? {})

  return (
    <section className="rounded-lg border border-slate-800 p-3">
      <header className="mb-2 flex items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-slate-200">{title}</h4>
        <span
          className={`rounded-full px-2 py-0.5 text-[10px] ${
            data?.enabled ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-700/50 text-slate-400'
          }`}
        >
          {data?.enabled ? 'live' : 'off'}
        </span>
      </header>

      <dl className="grid grid-cols-3 gap-2 text-xs">
        <div>
          <dt className="text-slate-500">Today</dt>
          <dd className={`font-semibold ${pnlClass(today.net_rupees)}`}>{rupees(today.net_rupees)}</dd>
          <dd className="text-slate-500">
            {today.closed ?? 0} closed{today.closed ? `, ${today.wins ?? 0} won` : ''}
          </dd>
        </div>
        <div>
          <dt className="text-slate-500">All time</dt>
          <dd className={`font-semibold ${pnlClass(all.net_rupees)}`}>{rupees(all.net_rupees)}</dd>
          <dd className="text-slate-500">{all.closed ?? 0} trades</dd>
        </div>
        <div>
          <dt className="text-slate-500">Open now</dt>
          <dd className="font-semibold text-slate-200">{open.length}</dd>
          <dd className="text-slate-500">{(data?.instruments ?? []).join(', ') || '—'}</dd>
        </div>
      </dl>

      {open.length ? (
        <ul className="mt-2 space-y-1">
          {open.map(([k, pos]) => (
            <li key={k} className="rounded bg-slate-900/50 px-2 py-1 font-mono text-[11px] text-slate-300">
              {k} · {String(pos.structure ?? pos.side ?? pos.dir ?? '')}{' '}
              {pos.short_k != null ? `${pos.short_k}/${pos.long_k ?? '—'}` : String(pos.strike ?? pos.entry ?? '')}
              {pos.max_loss_rupees != null ? ` · risk ${rupees(Number(pos.max_loss_rupees))}` : ''}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

export function LanesPanel() {
  const poll = usePollMs(20_000)

  const futures = useQuery({
    queryKey: ['lane-futures'],
    queryFn: () => api<LaneStatus>('/api/futures/status'),
    refetchInterval: poll,
  })
  const optionsCpr = useQuery({
    queryKey: ['lane-options-cpr'],
    queryFn: () => api<LaneStatus>('/api/options-cpr/status'),
    refetchInterval: poll,
  })

  const recent = [
    ...(optionsCpr.data?.recent_trades ?? []),
    ...(futures.data?.recent_trades ?? []),
  ]
    .sort((a, b) => String(b.exit_time ?? '').localeCompare(String(a.exit_time ?? '')))
    .slice(0, 8)

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        <LaneCard title="Directional futures (paper)" data={futures.data} />
        <LaneCard title="CPR options — buy + directional sell (paper)" data={optionsCpr.data} />
      </div>

      {recent.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-slate-500">
              <tr>
                <th className="py-1 pr-3 font-medium">Exit</th>
                <th className="py-1 pr-3 font-medium">Instrument</th>
                <th className="py-1 pr-3 font-medium">Structure</th>
                <th className="py-1 pr-3 font-medium">Reason</th>
                <th className="py-1 pr-3 text-right font-medium">Gross</th>
                <th className="py-1 pr-3 text-right font-medium">Charges</th>
                <th className="py-1 text-right font-medium">Net</th>
              </tr>
            </thead>
            <tbody className="text-slate-300">
              {recent.map((t, i) => {
                const net = Number(t.net_rupees ?? 0)
                const br = (t.charges_breakdown ?? {}) as Record<string, number>
                const chargeTip = Object.entries(br)
                  .filter(([k]) => k !== 'total')
                  .map(([k, v]) => `${k}: ₹${Number(v).toFixed(2)}`)
                  .join('\n')
                return (
                  <tr key={i} className="border-t border-slate-800/60">
                    <td className="py-1 pr-3 font-mono">{String(t.exit_time ?? '').slice(11, 16)}</td>
                    <td className="py-1 pr-3">{String(t.instrument ?? '')}</td>
                    <td className="py-1 pr-3">{String(t.structure ?? t.side ?? t.direction ?? '')}</td>
                    <td className="py-1 pr-3 text-slate-400">{String(t.exit_reason ?? '')}</td>
                    <td className="py-1 pr-3 text-right font-mono">{rupees(Number(t.gross_rupees ?? 0))}</td>
                    <td
                      className="py-1 pr-3 text-right font-mono text-slate-400"
                      title={chargeTip || undefined}
                    >
                      {rupees(Number(t.friction_rupees ?? 0))}
                    </td>
                    <td className={`py-1 text-right font-mono font-semibold ${pnlClass(net)}`}>
                      {rupees(net)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-slate-500">
          No paper trades logged yet. Enable a lane with ENABLE_FUTURES_PAPER or
          ENABLE_OPTIONS_CPR_PAPER.
        </p>
      )}
    </div>
  )
}
