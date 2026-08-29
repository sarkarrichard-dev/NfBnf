import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from '../../lib/cn'

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost'
type Size = 'sm' | 'md'

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-cyan-500 text-slate-950 border-cyan-400 hover:bg-cyan-400 ' +
    'active:bg-cyan-600 shadow-sm shadow-cyan-500/20 active:shadow-none',
  secondary:
    'bg-white/[0.04] text-slate-200 border-slate-700 hover:bg-white/[0.08] ' +
    'hover:border-slate-600 active:bg-white/[0.02]',
  danger:
    'bg-rose-500/10 text-rose-200 border-rose-800 hover:bg-rose-500/20 ' +
    'active:bg-rose-500/30',
  ghost:
    'bg-transparent text-slate-400 border-transparent hover:text-slate-200 ' +
    'hover:bg-white/[0.05] active:bg-white/[0.02]',
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
        'inline-flex select-none items-center justify-center rounded-md border font-medium',
        'transition-[transform,background-color,border-color,box-shadow] duration-100 ease-out',
        'active:scale-[0.97] motion-reduce:active:scale-100 motion-reduce:transition-none',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/70',
        'focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950',
        'disabled:pointer-events-none disabled:opacity-45',
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
        active && 'bg-cyan-500/15 text-cyan-200 hover:bg-cyan-500/20',
        className,
      )}
      aria-pressed={active}
      {...rest}
    />
  )
}
