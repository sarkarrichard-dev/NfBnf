import { useId } from 'react'

/** Cumulative-P&L area line. Baseline at zero, soft fill, an emphasised
 *  endpoint. Pure inline SVG, no deps. Colour follows the final value's sign. */
export function EquityCurve({
  values,
  height = 130,
  className,
}: {
  values: number[]
  height?: number
  className?: string
}) {
  const gid = useId().replace(/:/g, '')
  if (values.length < 2) {
    return (
      <div
        className={className}
        style={{ height }}
        aria-hidden
      />
    )
  }

  const W = 600
  const H = height
  const pad = 6
  const min = Math.min(0, ...values)
  const max = Math.max(0, ...values)
  const span = max - min || 1
  const stepX = (W - pad * 2) / (values.length - 1)
  const x = (i: number) => pad + i * stepX
  const y = (v: number) => pad + (1 - (v - min) / span) * (H - pad * 2)

  const line = values.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
  const area = `${line} L${x(values.length - 1).toFixed(1)} ${y(min).toFixed(1)} L${x(0).toFixed(1)} ${y(min).toFixed(1)} Z`
  const last = values[values.length - 1]
  const up = last >= 0
  const c = up ? 'var(--up)' : 'var(--down)'
  const zeroY = y(0)

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className={className}
      preserveAspectRatio="none"
      role="img"
      aria-label={`Cumulative P&L curve, ${values.length} points, ending at ${last.toFixed(0)}`}
    >
      <defs>
        <linearGradient id={`eq-${gid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={c} stopOpacity="0.22" />
          <stop offset="100%" stopColor={c} stopOpacity="0" />
        </linearGradient>
      </defs>
      {min < 0 && max > 0 ? (
        <line
          x1={pad}
          x2={W - pad}
          y1={zeroY}
          y2={zeroY}
          stroke="var(--hair)"
          strokeWidth="1"
          strokeDasharray="3 3"
        />
      ) : null}
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
    </svg>
  )
}
