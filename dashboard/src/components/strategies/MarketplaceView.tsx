import { cn } from '../../lib/cn'
import { fx } from '../../lib/theme'
import { STRATEGIES } from '../../lib/strategies'
import { useStrategyStatus } from '../../hooks/useStrategyStatus'
import { Button } from '../ui/Button'

/** Marketplace — the same catalog as deploy-able cards. Meaningful once
 *  subscriptions exist (Phase 4); for now Details opens the detail view and
 *  Deploy is the same read-only preview as the Builder. */
export function MarketplaceView({
  onDetails,
  onBuild,
}: {
  onDetails: (id: string) => void
  onBuild: (id: string) => void
}) {
  const status = useStrategyStatus()

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {STRATEGIES.map((s) => {
          const live = status[s.statusKey]
          return (
            <article key={s.id} className={cn(fx.panel, 'flex flex-col gap-3 p-4')}>
              <header className="flex items-start justify-between gap-2">
                <div>
                  <h3 className="text-sm font-bold text-slate-100">{s.name}</h3>
                  <p className="mt-0.5 font-mono text-[10px] uppercase tracking-wide text-slate-500">
                    {s.kind} · {s.timeframe}
                  </p>
                </div>
                <span
                  className={cn(
                    'rounded-full px-2 py-0.5 text-[10px] font-semibold',
                    live?.enabled ? 'bg-[var(--up)]/15 text-[var(--up)]' : 'bg-white/10 text-slate-400',
                  )}
                >
                  {live?.enabled ? 'running' : 'available'}
                </span>
              </header>

              <p className="flex-1 text-[11px] leading-relaxed text-slate-400 line-clamp-4">{s.blurb}</p>

              <dl className="grid grid-cols-2 gap-x-3 text-[11px]">
                <dt className="text-slate-500">Instrument</dt>
                <dd className="text-right text-slate-300">{s.instrument}</dd>
                <dt className="text-slate-500">Engine</dt>
                <dd className="text-right text-slate-300">{s.engine}</dd>
              </dl>

              <div className="flex gap-2">
                <Button variant="secondary" onClick={() => onDetails(s.id)}>
                  Details
                </Button>
                {s.builder ? (
                  <Button variant="primary" onClick={() => onBuild(s.id)}>
                    Deploy
                  </Button>
                ) : null}
              </div>
            </article>
          )
        })}

        <button
          type="button"
          onClick={() => onBuild('fvg_scalp')}
          className="flex min-h-[12rem] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-[var(--hair)] text-slate-400 transition-colors hover:border-[var(--acc)] hover:text-[var(--acc)]"
        >
          <span className="text-2xl leading-none">+</span>
          <span className="text-xs font-semibold">Build my own →</span>
        </button>
      </div>

      <p className="text-[11px] text-slate-600">
        The index lanes trade net-negative after real costs and are shown for completeness — see the
        Reports tab and <span className="text-slate-400">memory/strategy-findings.md</span>. Friction, not
        signal quality, is the binding constraint.
      </p>
    </div>
  )
}
