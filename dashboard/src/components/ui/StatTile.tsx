import { cn } from '../../lib/cn'
import { fx } from '../../lib/theme'
import { Sparkline } from '../Sparkline'

/** One metric card — label over value, with an optional sub-line and
 *  sparkline. The one stat-tile implementation for the whole dashboard;
 *  every screen previously rebuilt this by hand with small drifts between
 *  copies (Design audit 2026-09-14, principle #10). */
export function StatTile({
  label,
  value,
  sub,
  valueClass,
  points,
}: {
  label: string
  value: string
  sub?: string
  valueClass?: string
  points?: number[]
}) {
  return (
    <article className={fx.card}>
      <p className={fx.cardLabel}>{label}</p>
      <p className={cn(fx.cardValue, valueClass || 'text-slate-100')}>{value}</p>
      {sub ? <p className="mt-0.5 text-[11px] text-slate-500 tabular-nums">{sub}</p> : null}
      {points && points.length > 1 ? (
        <Sparkline points={points} className="mt-1 w-full" height={14} />
      ) : null}
    </article>
  )
}
