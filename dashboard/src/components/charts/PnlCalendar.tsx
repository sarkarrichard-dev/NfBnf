import { money } from '../../lib/pnl'

type Day = { period: string; pnl_rupees: number }

/** P&L calendar heat-grid — one cell per day, most recent bottom-right, colour
 *  by sign and intensity by size relative to the window's biggest day. Weeks
 *  run as rows (Mon–Sun), the Cryptomaty layout. */
export function PnlCalendar({
  days,
  weeks = 9,
}: {
  days: Day[]
  weeks?: number
}) {
  const byDate = new Map(days.map((d) => [d.period, Number(d.pnl_rupees) || 0]))
  const peak =
    Math.max(1, ...days.map((d) => Math.abs(Number(d.pnl_rupees) || 0))) || 1

  // Build a grid ending on the most recent Sunday-completed week.
  const today = new Date()
  const end = new Date(today)
  const dow = (end.getDay() + 6) % 7 // 0 = Mon
  end.setDate(end.getDate() + (6 - dow)) // → Sunday of this week
  const start = new Date(end)
  start.setDate(start.getDate() - (weeks * 7 - 1))

  const iso = (d: Date) => d.toISOString().slice(0, 10)
  const rows: { label: string; cells: { date: string; pnl?: number; future: boolean }[] }[] = []
  const cur = new Date(start)
  for (let w = 0; w < weeks; w++) {
    const cells = []
    let label = ''
    for (let i = 0; i < 7; i++) {
      const date = iso(cur)
      if (i === 0) label = cur.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
      cells.push({
        date,
        pnl: byDate.get(date),
        future: cur > today,
      })
      cur.setDate(cur.getDate() + 1)
    }
    rows.push({ label, cells })
  }

  const cellStyle = (pnl?: number): React.CSSProperties => {
    if (pnl == null) return { background: 'var(--hair-soft)' }
    if (pnl === 0) return { background: 'var(--hair)' }
    const t = Math.min(1, Math.abs(pnl) / peak)
    const alpha = 0.18 + t * 0.72
    return {
      background: `color-mix(in srgb, ${pnl > 0 ? 'var(--up)' : 'var(--down)'} ${Math.round(
        alpha * 100,
      )}%, transparent)`,
    }
  }

  return (
    <div className="overflow-x-auto">
      <div className="inline-grid gap-1" style={{ gridTemplateColumns: 'auto repeat(7, 1fr)' }}>
        <div />
        {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((d, i) => (
          <div key={i} className="text-center font-mono text-[9px] text-slate-600">
            {d}
          </div>
        ))}
        {rows.map((row, ri) => (
          <div key={ri} className="contents">
            <div className="pr-2 font-mono text-[9px] leading-4 text-slate-600">{row.label}</div>
            {row.cells.map((c) => (
              <div
                key={c.date}
                title={
                  c.future
                    ? undefined
                    : `${c.date} · ${c.pnl == null ? 'no trades' : money(c.pnl)}`
                }
                className="aspect-square min-w-3 rounded-[3px] border border-black/20"
                style={c.future ? { background: 'transparent', borderColor: 'transparent' } : cellStyle(c.pnl)}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-1.5 font-mono text-[9px] text-slate-500">
        <span>loss</span>
        <span className="size-2.5 rounded-[2px]" style={{ background: 'color-mix(in srgb, var(--down) 80%, transparent)' }} />
        <span className="size-2.5 rounded-[2px]" style={{ background: 'var(--hair)' }} />
        <span className="size-2.5 rounded-[2px]" style={{ background: 'color-mix(in srgb, var(--up) 80%, transparent)' }} />
        <span>gain</span>
      </div>
    </div>
  )
}
