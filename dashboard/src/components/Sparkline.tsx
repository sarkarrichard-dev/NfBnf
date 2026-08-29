/** Tiny inline-SVG sparkline. No deps. Colour follows last-vs-first. */
type Props = {
  points: number[]
  width?: number
  height?: number
  className?: string
  /** Force a colour instead of deriving it from the trend. */
  stroke?: string
}

export function Sparkline({ points, width = 72, height = 20, className, stroke }: Props) {
  if (points.length < 2) return null

  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min || 1
  const stepX = width / (points.length - 1)

  const d = points
    .map((v, i) => {
      const x = i * stepX
      const y = height - ((v - min) / span) * (height - 2) - 1
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  const trend = points[points.length - 1] - points[0]
  const color = stroke ?? (trend >= 0 ? '#34d399' : '#f87171')

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}
