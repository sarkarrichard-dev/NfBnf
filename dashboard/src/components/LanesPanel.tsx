import { pnlClass } from '../lib/pnl'

export type LaneStatus = {
  enabled?: boolean
  instruments?: string[]
  open_positions?: Record<string, Record<string, unknown>>
  today?: { closed?: number; net_rupees?: number; wins?: number }
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
