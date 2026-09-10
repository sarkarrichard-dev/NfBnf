import { useStickyTab } from '../../hooks/useStickyTab'
import { cn } from '../../lib/cn'
import { MyStrategiesView } from './MyStrategiesView'
import { BuilderView } from './BuilderView'
import { MarketplaceView } from './MarketplaceView'
import { ExchangesView } from './ExchangesView'
import { DeploymentsView } from './DeploymentsView'

const ALL_SUBTABS = [
  { id: 'mine', label: 'My strategies' },
  { id: 'builder', label: 'Builder' },
  { id: 'marketplace', label: 'Marketplace' },
  { id: 'exchanges', label: 'Exchanges' },
  { id: 'deployments', label: 'Deployments' },
] as const

/** The Strategies section — five sub-views behind one pill row. Sub-tab
 *  survives a refresh; the Builder can be reached from a Marketplace card
 *  via the `?strategy=` handoff below. `showBuilder` is server-driven
 *  (HIDE_STRATEGY_BUILDER) so a shared team view can drop the unfinished tab. */
export function StrategiesPage({ showBuilder = true }: { showBuilder?: boolean }) {
  const [sub, setSub] = useStickyTab('algo.strategies.sub', 'mine')
  const [seed, setSeed] = useStickyTab('algo.strategies.seed', 'fvg_scalp')

  const SUBTABS = ALL_SUBTABS.filter((t) => showBuilder || t.id !== 'builder')
  const view = !showBuilder && sub === 'builder' ? 'mine' : sub

  const openBuilder = (id: string) => {
    setSeed(id)
    setSub(showBuilder ? 'builder' : 'mine')
  }
  const openDetail = (id: string) => {
    setSeed(id)
    setSub('mine')
  }

  return (
    <div className="space-y-5">
      <div role="tablist" className="flex flex-wrap gap-1">
        {SUBTABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            type="button"
            aria-selected={view === t.id}
            onClick={() => setSub(t.id)}
            className={cn(
              'rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors',
              view === t.id
                ? 'bg-[var(--acc-soft)] text-[var(--acc)]'
                : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-100',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {view === 'mine' ? (
        <MyStrategiesView
          seedId={seed}
          onSeed={setSeed}
          onBuild={openBuilder}
          showBuilder={showBuilder}
        />
      ) : null}
      {view === 'builder' ? <BuilderView seedId={seed} /> : null}
      {view === 'marketplace' ? (
        <MarketplaceView onDetails={openDetail} onBuild={openBuilder} showBuilder={showBuilder} />
      ) : null}
      {view === 'exchanges' ? <ExchangesView /> : null}
      {view === 'deployments' ? <DeploymentsView /> : null}
    </div>
  )
}
