import { useStickyTab } from '../../hooks/useStickyTab'
import { cn } from '../../lib/cn'
import { MyStrategiesView } from './MyStrategiesView'
import { BuilderView } from './BuilderView'
import { MarketplaceView } from './MarketplaceView'
import { ExchangesView } from './ExchangesView'
import { DeploymentsView } from './DeploymentsView'

const SUBTABS = [
  { id: 'mine', label: 'My strategies' },
  { id: 'builder', label: 'Builder' },
  { id: 'marketplace', label: 'Marketplace' },
  { id: 'exchanges', label: 'Exchanges' },
  { id: 'deployments', label: 'Deployments' },
] as const

/** The Strategies section — five sub-views behind one pill row. Sub-tab
 *  survives a refresh; the Builder can be reached from a Marketplace card
 *  via the `?strategy=` handoff below. */
export function StrategiesPage() {
  const [sub, setSub] = useStickyTab('algo.strategies.sub', 'mine')
  const [seed, setSeed] = useStickyTab('algo.strategies.seed', 'fvg_scalp')

  const openBuilder = (id: string) => {
    setSeed(id)
    setSub('builder')
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
            aria-selected={sub === t.id}
            onClick={() => setSub(t.id)}
            className={cn(
              'rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors',
              sub === t.id
                ? 'bg-[var(--acc-soft)] text-[var(--acc)]'
                : 'text-slate-400 hover:bg-white/[0.04] hover:text-slate-100',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {sub === 'mine' ? <MyStrategiesView seedId={seed} onSeed={setSeed} onBuild={openBuilder} /> : null}
      {sub === 'builder' ? <BuilderView seedId={seed} /> : null}
      {sub === 'marketplace' ? <MarketplaceView onDetails={openDetail} onBuild={openBuilder} /> : null}
      {sub === 'exchanges' ? <ExchangesView /> : null}
      {sub === 'deployments' ? <DeploymentsView /> : null}
    </div>
  )
}
