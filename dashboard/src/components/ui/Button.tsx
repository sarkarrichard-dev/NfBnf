import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '../../lib/cn'

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'
type Size = 'sm' | 'md'

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-[var(--acc-strong)] text-white border-[var(--acc-strong)] ' +
    'hover:brightness-110 active:brightness-95',
  secondary:
    'bg-white/[0.04] text-slate-100 border-[var(--hair)] hover:bg-white/[0.08] ' +
    'hover:border-white/20 active:bg-white/[0.03]',
  danger:
    'bg-[var(--armed)]/15 text-rose-100 border-[var(--armed)]/60 ' +
    'hover:bg-[var(--armed)]/25 active:bg-[var(--armed)]/35',
  ghost:
    'bg-transparent text-slate-400 border-transparent hover:text-slate-100 ' +
    'hover:bg-white/[0.06] active:bg-white/[0.02]',
}

const SIZES: Record<Size, string> = {
  sm: 'px-2.5 py-1 text-xs gap-1.5',
  md: 'px-3.5 py-2 text-sm gap-2',
}

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant
  size?: Size
  pending?: boolean
  icon?: ReactNode
}

/**
 * The one button primitive.
 *
 * Press feedback is the point: every button dips (`active:scale`) and shifts
 * colour on press, with a 100ms transition so it reads as a physical action
 * rather than a state change that already happened. `motion-reduce` opts out of
 * the transform for people who ask the OS to reduce motion.
 *
 * `pending` disables the button and shows a spinner in place of the icon, so an
 * in-flight request can't be double-fired — the most common way a trading UI
 * sends two orders.
 */
export function Button({
  variant = 'secondary',
  size = 'sm',
  pending = false,
  icon,
  disabled,
  className,
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      className={cn(
        'inline-flex select-none items-center justify-center rounded-lg border font-semibold',
        'transition-[transform,background-color,border-color,filter] duration-100 ease-out',
        'active:scale-[0.97] motion-reduce:active:scale-100 motion-reduce:transition-none',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--acc)]/70',
        'focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--ground)]',
        // Visibly disabled on a near-black ground: dim alone reads as "gone", so
        // also flatten the fill and switch the cursor.
        'disabled:pointer-events-none disabled:opacity-55 disabled:saturate-50 disabled:cursor-not-allowed',
        'aria-[busy=true]:opacity-100 aria-[busy=true]:saturate-100',
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {pending ? (
        <span
          aria-hidden
          className="size-3 animate-spin rounded-full border-[1.5px] border-current border-t-transparent"
        />
      ) : (
        icon
      )}
      {children}
    </button>
  )
}

/** Segmented control — used for period pickers and tab-like switches. */
export function ButtonGroup({ children }: { children: ReactNode }) {
  return (
    <div className="inline-flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-900/50 p-1">
      {children}
    </div>
  )
}

export function ToggleButton({
  active,
  className,
  ...rest
}: ButtonProps & { active?: boolean }) {
  return (
    <Button
      variant="ghost"
      className={cn(
        'rounded border-0',
        active && 'bg-[var(--acc-soft)] text-[var(--acc)] hover:brightness-110',
        className,
      )}
      aria-pressed={active}
      {...rest}
    />
  )
}
