import { cn } from '../../lib/cn'

/** The QuantHawk mark — a hawk swept into an upward peak (wings rising = the
 *  bullish read) with a candlestick spine. Wings/head inherit `currentColor`
 *  (the silver plumage); the candle spine is money-green and the eye is
 *  hawk-gold. Legible down to 16px. */
export function HawkMark({ className, title }: { className?: string; title?: string }) {
  return (
    <svg
      viewBox="0 0 40 40"
      className={cn('h-6 w-6', className)}
      fill="none"
      role={title ? 'img' : 'presentation'}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      <g fill="currentColor">
        <path d="M20 12.4C15.1 13.2 9.7 16.1 5 21.6c-1.1 1.3-.2 2.8 1.4 2.3 5-1.7 9.6-2.3 12.7-1.6.6.1.9-.3.9-.9z" />
        <path d="M20 12.4c4.9.8 10.3 3.7 15 9.2 1.1 1.3.2 2.8-1.4 2.3-5-1.7-9.6-2.3-12.7-1.6-.6.1-.9-.3-.9-.9z" />
        <path d="M20 7.2l2.7 4.5c-.8.6-1.7.9-2.7.9s-1.9-.3-2.7-.9z" />
      </g>
      <path
        d="M20 14.1c.9 0 1.8-.2 2.5-.6l-1.4 8.8 2 6.9c.15.5-.4.9-.8.55L20 26.9l-1.5 1.85c-.4.35-.95-.05-.8-.55l2-6.9-1.4-8.8c.7.4 1.6.6 2.5.6z"
        fill="var(--up)"
      />
      <circle cx="24.4" cy="10.1" r="1.15" fill="var(--acc)" />
    </svg>
  )
}

/** Mark + wordmark, for the top bar. */
export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2 select-none', className)}>
      <span className="grid size-7 place-items-center rounded-lg border border-[var(--acc)]/40 bg-white/[0.04] text-slate-100">
        <HawkMark className="h-[19px] w-[19px]" />
      </span>
      <span className="text-[15px] font-extrabold tracking-[-0.02em] text-slate-50">
        Quant<span className="text-[var(--acc)]">Hawk</span>
      </span>
    </span>
  )
}
