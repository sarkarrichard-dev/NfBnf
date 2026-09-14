import { cn } from '../lib/cn'
import { pnlClass } from '../lib/pnl'

type OpenPosition = {
  dir?: string
  entry?: number
  stop?: number
  entry_time?: string
  mark?: number | null
  unrealized_rupees?: number | null
  unrealized_pct?: number | null
}

export type LaneStatus = {
  enabled?: boolean
  instruments?: string[]
  open_positions?: Record<string, OpenPosition>
  today?: { closed?: number; net_rupees?: number; wins?: number; open_unrealized_rupees?: number }
  all_time?: { closed?: number; net_rupees?: number }
}

/** Whole-rupee amount, no decimals, no leading "+" — the plain "how much
 *  money" format used everywhere a P&L figure doesn't need money()'s sign
 *  emphasis. One implementation; four screens used to redefine this by hand. */
export const rupees = (v?: number | null) =>
  v == null ? '—' : `${v < 0 ? '-' : ''}₹${Math.abs(Math.round(v)).toLocaleString('en-IN')}`

export { pnlClass }

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
            data?.enabled ? 'bg-[var(--up)]/15 text-[var(--up)]' : 'bg-slate-700/50 text-slate-400'
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
        <div className="mt-2 overflow-x-auto rounded-lg border border-slate-800 bg-black/25">
          <table className="min-w-full text-[11px]">
            <thead className="text-slate-500">
              <tr className="border-b border-slate-800 [&>th]:px-2.5 [&>th]:py-1.5 [&>th]:text-left [&>th]:font-medium">
                <th>Instrument</th>
                <th>Side</th>
                <th>Entry → Mark</th>
                <th>Stop</th>
                <th className="text-right">Unrealised</th>
              </tr>
            </thead>
            <tbody className="font-mono text-slate-300">
              {open.map(([k, pos]) => (
                <tr key={k} className="border-b border-slate-800/60 [&>td]:px-2.5 [&>td]:py-1.5">
                  <td className="font-sans font-medium text-slate-100">{k}</td>
                  <td className={pos.dir === 'LONG' ? 'text-[var(--up)]' : 'text-[var(--down)]'}>
                    {pos.dir ?? '—'}
                  </td>
                  <td className="tabular-nums">
                    {pos.entry?.toFixed(2) ?? '—'} → {pos.mark != null ? pos.mark.toFixed(2) : '—'}
                  </td>
                  <td className="tabular-nums text-slate-500">{pos.stop?.toFixed(2) ?? '—'}</td>
                  <td className={cn('text-right tabular-nums font-semibold', pnlClass(pos.unrealized_rupees))}>
                    {pos.unrealized_rupees != null
                      ? `${rupees(pos.unrealized_rupees)}${pos.unrealized_pct != null ? ` (${pos.unrealized_pct > 0 ? '+' : ''}${pos.unrealized_pct.toFixed(2)}%)` : ''}`
                      : 'no live mark'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}
