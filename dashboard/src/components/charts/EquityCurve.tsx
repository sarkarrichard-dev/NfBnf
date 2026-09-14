import { useId, useRef, useState } from 'react'
import { money } from '../../lib/pnl'

export type EquityPoint = { date: string; value: number }

function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) return [min]
  const span = max - min
  const raw = span / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const norm = raw / mag
  const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag
  const start = Math.ceil(min / step) * step
  const ticks: number[] = []
  for (let v = start; v <= max + step * 1e-6; v += step) ticks.push(Math.round(v * 100) / 100)
  return ticks
}

function shortDate(iso: string): string {
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/** Cumulative-P&L area line — gridlines, axis labels, and a hover crosshair
 *  with a tooltip, in the spirit of a TradingView-style chart. Pure inline
 *  SVG, no deps; hit-testing works in real pixel space via the wrapping div
 *  so it doesn't need to know the SVG's internal viewBox scale. */
export function EquityCurve({
  points,
  height = 130,
  className,
}: {
  points: EquityPoint[]
  height?: number
  className?: string
}) {
  const gid = useId().replace(/:/g, '')
  const wrapRef = useRef<HTMLDivElement>(null)
  const [hover, setHover] = useState<number | null>(null)

  if (points.length < 2) {
    return <div className={className} style={{ height }} aria-hidden />
  }

  const values = points.map((p) => p.value)
  const W = 600
  const H = height
  const padL = 44
  const padR = 8
  const padT = 8
  const padB = 18
  const min = Math.min(0, ...values)
  const max = Math.max(0, ...values)
  const span = max - min || 1
  const stepX = (W - padL - padR) / (points.length - 1)
  const x = (i: number) => padL + i * stepX
  const y = (v: number) => padT + (1 - (v - min) / span) * (H - padT - padB)

  const line = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
  const area = `${line} L${x(values.length - 1).toFixed(1)} ${y(min).toFixed(1)} L${x(0).toFixed(1)} ${y(min).toFixed(1)} Z`
  const last = values[values.length - 1]
  const up = last >= 0
  const c = up ? 'var(--up)' : 'var(--down)'
  const yTicks = niceTicks(min, max)

  const onMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = wrapRef.current?.getBoundingClientRect()
    if (!rect || rect.width === 0) return
    const frac = (e.clientX - rect.left) / rect.width
    const i = Math.max(0, Math.min(points.length - 1, Math.round(frac * (points.length - 1))))
    setHover(i)
  }

  const hp = hover != null ? points[hover] : null
  const hoverLeftPct = hover != null ? (x(hover) / W) * 100 : 0

  return (
    <div
      ref={wrapRef}
      className={className}
      style={{ position: 'relative', height }}
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height="100%"
        preserveAspectRatio="none"
        role="img"
        aria-label={`Cumulative P&L curve, ${points.length} points, ending at ${last.toFixed(0)}`}
      >
        <defs>
          <linearGradient id={`eq-${gid}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={c} stopOpacity="0.22" />
            <stop offset="100%" stopColor={c} stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* horizontal gridlines + y-axis labels */}
        {yTicks.map((t) => (
          <g key={t}>
            <line
              x1={padL}
              x2={W - padR}
              y1={y(t)}
              y2={y(t)}
              stroke={t === 0 ? 'var(--hair)' : 'var(--hair-soft)'}
              strokeWidth="1"
              strokeDasharray={t === 0 ? '3 3' : undefined}
              vectorEffect="non-scaling-stroke"
            />
            <text
              x={padL - 6}
              y={y(t)}
              textAnchor="end"
              dominantBaseline="middle"
              fontSize="9"
              fontFamily="monospace"
              fill="var(--hair)"
              stroke="none"
            >
              {money(t)}
            </text>
          </g>
        ))}

        {/* x-axis: first and last date */}
        <text x={padL} y={H - 4} fontSize="9" fontFamily="monospace" fill="var(--hair)">
          {shortDate(points[0].date)}
        </text>
        <text x={W - padR} y={H - 4} textAnchor="end" fontSize="9" fontFamily="monospace" fill="var(--hair)">
          {shortDate(points[points.length - 1].date)}
        </text>

        <path d={area} fill={`url(#eq-${gid})`} />
        <path
          d={line}
          fill="none"
          stroke={c}
          strokeWidth="1.75"
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
        <circle cx={x(values.length - 1)} cy={y(last)} r="3" fill={c} />

        {hover != null ? (
          <>
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1={padT}
              y2={H - padB}
              stroke="var(--hair)"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
            <circle cx={x(hover)} cy={y(values[hover])} r="3.5" fill={c} stroke="var(--ground)" strokeWidth="1.5" />
          </>
        ) : null}
      </svg>

      {hp ? (
        <div
          className="pointer-events-none absolute top-1 z-10 -translate-x-1/2 whitespace-nowrap rounded-md border border-[var(--hair)] bg-[var(--panel)] px-2 py-1 text-[10px] shadow-lg"
          style={{
            left: `${Math.min(92, Math.max(8, hoverLeftPct))}%`,
          }}
        >
          <div className="text-slate-500">{shortDate(hp.date)}</div>
          <div className={hp.value >= 0 ? 'font-semibold text-[var(--up)]' : 'font-semibold text-[var(--down)]'}>
            {money(hp.value)}
          </div>
        </div>
      ) : null}
    </div>
  )
}
