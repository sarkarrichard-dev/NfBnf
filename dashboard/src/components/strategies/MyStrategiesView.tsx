import { cn } from '../../lib/cn'
import { fx } from '../../lib/theme'
import { STRATEGIES, byId, renderReads, sideLabel, type StrategyDef, type ParamGroup } from '../../lib/strategies'
import { useStrategyStatus, type LiveStatus } from '../../hooks/useStrategyStatus'
import { rupees } from '../LanesPanel'
import { Button } from '../ui/Button'

function StatusChip({ s }: { s?: LiveStatus }) {
  const on = !!s?.enabled
  return (
    <span
      className={cn(
        'rounded-full px-2 py-0.5 text-[10px] font-semibold',
        on ? 'bg-[var(--up)]/15 text-[var(--up)]' : 'bg-white/10 text-slate-400',
      )}
    >
      {on ? `${s?.mode ?? 'PAPER'} · running` : 'dormant'}
    </span>
  )
}

const GROUPS: { id: ParamGroup; label: string }[] = [
  { id: 'market', label: 'Market' },
  { id: 'signal', label: 'Signal' },
  { id: 'risk', label: 'Risk & size' },
]

function Detail({
  def,
  live,
  onBuild,
  showBuilder,
  showInternals,
}: {
  def: StrategyDef
  live?: LiveStatus
  onBuild: (id: string) => void
  showBuilder: boolean
  showInternals: boolean
}) {
  const defaults = Object.fromEntries(def.params.map((p) => [p.key, p.default])) as Record<string, number | boolean | string>
  // Redacted team view: entry logic is hidden, only the result + exit trail stay.
  const groups = showInternals ? GROUPS : GROUPS.filter((g) => g.id === 'risk')

  return (
    <section className={cn(fx.panel, 'p-4 space-y-4')}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-base font-bold text-slate-100">{def.name}</h3>
          <p className="mt-0.5 font-mono text-[11px] uppercase tracking-wide text-slate-500">
            {showInternals ? `${def.kind} · ${def.instrument} · ${def.timeframe}` : sideLabel(def)}
          </p>
        </div>
        <StatusChip s={live} />
      </header>

      <p className="text-xs leading-relaxed text-slate-400">{showInternals ? def.blurb : def.teaser}</p>

      {showInternals ? (
        <div className="rounded-md border border-[var(--hair-soft)] bg-white/[0.015] p-3">
          <p className={fx.cardLabel}>Reads</p>
          <p className="mt-1 text-xs leading-relaxed text-slate-200">{renderReads(def.reads, defaults)}</p>
        </div>
      ) : null}

      {def.backtest ? (
        <div className="grid grid-cols-3 gap-2">
          <div className={fx.card}>
            <p className={fx.cardLabel}>Window</p>
            <p className={cn(fx.cardValue, 'text-xs')}>{def.backtest.window}</p>
          </div>
          <div className={fx.card}>
            <p className={fx.cardLabel}>Net</p>
            <p className={cn(fx.cardValue, 'text-xs')}>{def.backtest.net}</p>
          </div>
          <div className={fx.card}>
            <p className={fx.cardLabel}>Today (paper)</p>
            <p className={cn(fx.cardValue, 'text-xs')}>{rupees(live?.today?.net_rupees)}</p>
          </div>
        </div>
      ) : null}
      {def.backtest?.note ? <p className="text-[11px] text-slate-600">{def.backtest.note}</p> : null}

      {def.params.length && groups.length ? (
        <div className="space-y-3">
          {groups.map((g) => {
            const rows = def.params.filter((p) => p.group === g.id)
            if (!rows.length) return null
            return (
              <div key={g.id}>
                <p className={fx.cardLabel}>{g.label}</p>
                <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
                  {rows.map((p) => (
                    <div key={p.key} className="flex items-baseline justify-between gap-2 border-b border-[var(--hair-soft)] py-1">
                      <dt className="text-[11px] text-slate-500">{p.label}</dt>
                      <dd className="font-mono text-xs tabular-nums text-slate-200">{String(p.default)}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )
          })}
        </div>
      ) : showInternals ? (
        <p className="text-[11px] text-slate-600">
          Tuned through <span className="text-slate-400">Settings → Strategy tuning (.env)</span>.
        </p>
      ) : null}

      {def.builder && showBuilder ? (
        <Button variant="secondary" onClick={() => onBuild(def.id)}>
          Open in Builder →
        </Button>
      ) : null}
    </section>
  )
}

export function MyStrategiesView({
  seedId,
  onSeed,
  onBuild,
  showBuilder = true,
  showInternals = true,
}: {
  seedId: string
  onSeed: (id: string) => void
  onBuild: (id: string) => void
  showBuilder?: boolean
  showInternals?: boolean
}) {
  const status = useStrategyStatus()
  const active = byId(seedId) ?? STRATEGIES[0]

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,20rem),1fr]">
      <ul className="space-y-1.5">
        {STRATEGIES.map((s) => {
          const live = status[s.statusKey]
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onSeed(s.id)}
                className={cn(
                  'w-full rounded-sm border px-3 py-2.5 text-left transition-colors',
                  s.id === active.id
                    ? 'border-[var(--acc)] bg-[var(--acc-soft)]'
                    : 'border-[var(--hair-soft)] hover:border-[var(--hair)] hover:bg-white/[0.02]',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-100">{s.name}</span>
                  <StatusChip s={live} />
                </div>
                <p className="mt-0.5 font-mono text-[10.5px] text-slate-500">
                  {showInternals ? s.engine : sideLabel(s)}
                </p>
              </button>
            </li>
          )
        })}
      </ul>

      <Detail
        def={active}
        live={status[active.statusKey]}
        onBuild={onBuild}
        showBuilder={showBuilder}
        showInternals={showInternals}
      />
    </div>
  )
}
