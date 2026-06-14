import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { AutoTraderPanel } from './components/AutoTraderPanel'
import { BacktestPanel } from './components/BacktestPanel'
import { CollapsibleSection } from './components/CollapsibleSection'
import { DhanAccountPanel } from './components/DhanAccountPanel'
import { DhanAuthPanel } from './components/DhanAuthPanel'
import { ExecutionPanel } from './components/ExecutionPanel'
import { Header } from './components/Header'
import { LearningPanel } from './components/LearningPanel'
import { PositionsPanel } from './components/PositionsPanel'
import { PeriodTabs, StatsRail } from './components/StatsRail'
import { StrategyTuningPanel } from './components/StrategyTuningPanel'
import { TradeLogTable } from './components/TradeLogTable'
import { useDashboardData } from './hooks/useDashboardData'
import { api } from './lib/api'
import type { PeriodKey, PositionsFilter } from './types/analytics'

type StatusResponse = {
  trading_mode: string
  kill_switch?: { active?: boolean; reasons?: string[] }
  policy?: {
    lots_per_trade?: number
    max_daily_loss_rupees?: number
    order_quantities?: Record<string, number>
  }
  market?: { message?: string; now_ist?: string; is_open?: boolean; phase?: string }
  strategy?: Record<string, unknown>
  dhan_ready?: boolean
  execution_gates?: Array<{ title?: string; detail?: string; ok?: boolean }>
}

function App() {
  const [period, setPeriod] = useState<PeriodKey>('today')
  const [positionsFilter, setPositionsFilter] = useState<PositionsFilter>('all')

  const status = useQuery({
    queryKey: ['status'],
    queryFn: () => api<StatusResponse>('/api/status'),
    refetchInterval: 15_000,
  })

  const dashboard = useDashboardData()

  const ks = status.data?.kill_switch
  const refreshLabel = [
    dashboard.statsUpdatedAt ? `Stats ${dashboard.statsUpdatedAt}` : '',
    dashboard.hasOpen && dashboard.mtmUpdatedAt ? `MTM ${dashboard.mtmUpdatedAt}` : '',
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <main className="mx-auto max-w-[90rem] px-4 py-6 md:px-8">
      <Header tradingMode={status.data?.trading_mode} market={status.data?.market} />

      {ks?.active ? (
        <p className="mb-4 rounded-md border border-red-500/30 bg-red-950/30 px-3 py-2 text-sm text-red-200">
          Kill switch active: {(ks.reasons || []).join(' ')}
        </p>
      ) : null}

      {dashboard.isError ? (
        <p className="mb-4 rounded-lg border border-red-500/30 bg-red-950/20 px-3 py-2 text-sm text-red-200">
          {dashboard.error instanceof Error ? dashboard.error.message : 'Failed to load analytics'}
        </p>
      ) : null}

      <PeriodTabs period={period} onChange={setPeriod} updatedLabel={refreshLabel} />

      <StatsRail
        analytics={dashboard.analytics}
        period={period}
        openMtmRupees={dashboard.openMtmRupees}
      />

      <div className="mb-6">
        <ExecutionPanel
          tradingMode={status.data?.trading_mode}
          lotsPerTrade={status.data?.policy?.lots_per_trade}
          orderQuantities={status.data?.policy?.order_quantities}
          liveSummary={dashboard.analytics?.live_summary}
          gates={status.data?.execution_gates}
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-[1.6fr,1fr]">
        <div className="space-y-6">
          <PositionsPanel
            logRows={dashboard.logRows}
            trades={dashboard.trades}
            period={period}
            filter={positionsFilter}
            onFilterChange={setPositionsFilter}
            mtmUpdatedAt={dashboard.mtmUpdatedAt}
          />
          <TradeLogTable
            logRows={dashboard.logRows}
            trades={dashboard.trades}
            period={period}
            mtmUpdatedAt={dashboard.mtmUpdatedAt}
          />
        </div>

        <aside className="space-y-4">
          <CollapsibleSection title="Dhan account (live)" defaultOpen>
            <DhanAccountPanel />
          </CollapsibleSection>
          <CollapsibleSection title="Dhan login & token">
            <DhanAuthPanel />
          </CollapsibleSection>
          <CollapsibleSection title="Auto trader & scanner" defaultOpen>
            <AutoTraderPanel />
          </CollapsibleSection>
          <CollapsibleSection title="Strategy tuning (.env)">
            <StrategyTuningPanel strategy={status.data?.strategy} />
          </CollapsibleSection>
          <CollapsibleSection title="Backtest (Dhan intraday)">
            <BacktestPanel />
          </CollapsibleSection>
          <CollapsibleSection title="Learning from outcomes">
            <LearningPanel />
          </CollapsibleSection>
        </aside>
      </div>
    </main>
  )
}

export default App
