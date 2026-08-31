import { lazy, Suspense, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CollapsibleSection } from './components/CollapsibleSection'
import { Tabs } from './components/ui/Tabs'
import { useStickyTab } from './hooks/useStickyTab'
import { OperationsPanel } from './components/OperationsPanel'
import { ExecutionPanel } from './components/ExecutionPanel'
import { Header } from './components/Header'
import { JournalPanel } from './components/JournalPanel'
import { StatsOverview } from './components/StatsRail'
import { useDashboardData } from './hooks/useDashboardData'
import { usePollMs } from './hooks/usePageVisible'
import { api } from './lib/api'
import type { PeriodKey } from './types/analytics'

const DhanAccountPanel = lazy(() =>
  import('./components/DhanAccountPanel').then((m) => ({ default: m.DhanAccountPanel })),
)
const DhanAuthPanel = lazy(() =>
  import('./components/DhanAuthPanel').then((m) => ({ default: m.DhanAuthPanel })),
)
const AutoTraderPanel = lazy(() =>
  import('./components/AutoTraderPanel').then((m) => ({ default: m.AutoTraderPanel })),
)
const StrategyTuningPanel = lazy(() =>
  import('./components/StrategyTuningPanel').then((m) => ({ default: m.StrategyTuningPanel })),
)
const BacktestPanel = lazy(() =>
  import('./components/BacktestPanel').then((m) => ({ default: m.BacktestPanel })),
)
const LearningPanel = lazy(() =>
  import('./components/LearningPanel').then((m) => ({ default: m.LearningPanel })),
)
const BrainPanel = lazy(() =>
  import('./components/BrainPanel').then((m) => ({ default: m.BrainPanel })),
)
const FeaturesPanel = lazy(() =>
  import('./components/FeaturesPanel').then((m) => ({ default: m.FeaturesPanel })),
)
const LanesPanel = lazy(() =>
  import('./components/LanesPanel').then((m) => ({ default: m.LanesPanel })),
)
const DayReviewPanel = lazy(() =>
  import('./components/DayReviewPanel').then((m) => ({ default: m.DayReviewPanel })),
)

type StatusResponse = {
  trading_mode: string
  kill_switch?: { active?: boolean; reasons?: string[] }
  policy?: {
    lots_per_trade?: number
    order_quantities?: Record<string, number>
  }
  market?: { message?: string; now_ist?: string; is_open?: boolean; phase?: string }
  strategy?: Record<string, unknown>
  dhan_ready?: boolean
  live_allowed?: boolean
  trading_gates?: {
    can_send_live_orders?: boolean
    trading_mode?: string
    reasons?: Array<{ title?: string; detail?: string }>
  }
  auto?: {
    running?: boolean
    last_error?: string
  }
}

function PanelFallback() {
  return <p className="text-xs text-slate-500">Loading…</p>
}

function App() {
  const [period, setPeriod] = useState<PeriodKey>('today')
  const statusPoll = usePollMs(20_000)

  const status = useQuery({
    queryKey: ['status'],
    queryFn: () => api<StatusResponse>('/api/status'),
    refetchInterval: statusPoll,
    staleTime: 10_000,
  })

  const dashboard = useDashboardData()
  const [tab, setTab] = useStickyTab('algo.tab', 'trade')
  const openCount = useMemo(
    () => (dashboard.trades ?? []).filter((t) => t.is_open).length,
    [dashboard.trades],
  )

  const gateReasons = status.data?.trading_gates?.reasons
  const gates = useMemo(
    () =>
      gateReasons?.map((r) => ({
        title: r.title,
        detail: r.detail,
        ok:
          r.title === 'Paper mode (default)' ||
          r.title === 'Buy options' ||
          r.title === 'Sell options' ||
          r.title === 'Kill switch (Live only)' ||
          r.title === 'Kill switch (would block Live)',
      })),
    [gateReasons],
  )

  const ks = status.data?.kill_switch
  const refreshLabel = [
    dashboard.statsUpdatedAt ? `Stats ${dashboard.statsUpdatedAt}` : '',
    dashboard.hasOpen && dashboard.mtmUpdatedAt ? `MTM ${dashboard.mtmUpdatedAt}` : '',
    dashboard.isFetching ? 'syncing…' : '',
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <main className="mx-auto max-w-[90rem] px-4 py-6 md:px-8">
      <Header
        tradingMode={status.data?.trading_mode}
        market={status.data?.market}
        dhanReady={status.data?.dhan_ready}
      />

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

      <Tabs
        tabs={[
          { id: 'trade', label: 'Trade', badge: openCount || null },
          { id: 'strategies', label: 'Strategies' },
          { id: 'research', label: 'Research' },
          { id: 'setup', label: 'Setup' },
        ]}
        value={tab}
        onChange={setTab}
      />

      {tab === 'trade' ? (
        <>
          <StatsOverview
            period={period}
            onPeriodChange={setPeriod}
            updatedLabel={refreshLabel}
            marketMessage={status.data?.market?.message}
            marketOpen={status.data?.market?.is_open}
            analytics={dashboard.analytics}
            openMtmRupees={dashboard.openMtmRupees}
            isLoading={dashboard.isLoading}
          />
          <div className="mb-6">
            <ExecutionPanel
              tradingMode={status.data?.trading_mode}
              lotsPerTrade={status.data?.policy?.lots_per_trade}
              orderQuantities={status.data?.policy?.order_quantities}
              liveSummary={dashboard.analytics?.live_summary}
              paperSummary={dashboard.analytics?.paper_summary}
              gates={gates}
              liveArmed={status.data?.live_allowed}
            />
          </div>
          <div className="mb-6">
            <Suspense fallback={<PanelFallback />}>
              <DayReviewPanel />
            </Suspense>
          </div>
          <div className="grid gap-6 xl:grid-cols-[1.6fr,1fr]">
            <JournalPanel
              logRows={dashboard.logRows}
              trades={dashboard.trades}
              period={period}
              mtmUpdatedAt={dashboard.mtmUpdatedAt}
            />
            <aside className="space-y-4">
              <CollapsibleSection title="Auto trader & scanner" defaultOpen>
                <Suspense fallback={<PanelFallback />}>
                  <AutoTraderPanel />
                </Suspense>
              </CollapsibleSection>
              <CollapsibleSection title="Dhan broker account" defaultOpen>
                <Suspense fallback={<PanelFallback />}>
                  <DhanAccountPanel tradingMode={status.data?.trading_mode} />
                </Suspense>
              </CollapsibleSection>
            </aside>
          </div>
        </>
      ) : null}

      {tab === 'strategies' ? (
        <div className="grid gap-6 xl:grid-cols-[1.6fr,1fr]">
          <CollapsibleSection title="Strategy lanes (paper)" defaultOpen>
            <Suspense fallback={<PanelFallback />}>
              <LanesPanel />
            </Suspense>
          </CollapsibleSection>
          <aside className="space-y-4">
            <CollapsibleSection title="AI brain & ML gate" defaultOpen>
              <Suspense fallback={<PanelFallback />}>
                <BrainPanel />
              </Suspense>
            </CollapsibleSection>
          </aside>
        </div>
      ) : null}

      {tab === 'research' ? (
        <div className="grid gap-6 xl:grid-cols-2">
          <CollapsibleSection title="Backtest (Dhan intraday)" defaultOpen>
            <Suspense fallback={<PanelFallback />}>
              <BacktestPanel />
            </Suspense>
          </CollapsibleSection>
          <CollapsibleSection title="Learning from outcomes" defaultOpen>
            <Suspense fallback={<PanelFallback />}>
              <LearningPanel />
            </Suspense>
          </CollapsibleSection>
        </div>
      ) : null}

      {tab === 'setup' ? (
        <div className="space-y-6">
          <div className="grid gap-6 xl:grid-cols-2">
            <CollapsibleSection title="Dhan login & token" defaultOpen>
              <Suspense fallback={<PanelFallback />}>
                <DhanAuthPanel />
              </Suspense>
            </CollapsibleSection>
            <CollapsibleSection title="Feature toggles" defaultOpen>
              <Suspense fallback={<PanelFallback />}>
                <FeaturesPanel />
              </Suspense>
            </CollapsibleSection>
            <CollapsibleSection title="Strategy tuning (.env)" defaultOpen>
              <Suspense fallback={<PanelFallback />}>
                <StrategyTuningPanel strategy={status.data?.strategy} />
              </Suspense>
            </CollapsibleSection>
          </div>
          <CollapsibleSection title="Diagnostics & scanner log">
            <OperationsPanel />
          </CollapsibleSection>
        </div>
      ) : null}

    </main>
  )
}

export default App
