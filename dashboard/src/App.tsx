import { lazy, Suspense, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CollapsibleSection } from './components/CollapsibleSection'
import { AppShell } from './components/shell/AppShell'
import { PageHeader } from './components/shell/PageHeader'
import { StatusPills } from './components/shell/StatusPills'
import type { NavGroup } from './components/shell/Sidebar'
import {
  IconGrid,
  IconLayers,
  IconCoin,
  IconFlask,
  IconGear,
  IconChart,
  IconList,
} from './components/ui/Icons'
import { ExecutionPanel } from './components/ExecutionPanel'
import { JournalPanel } from './components/JournalPanel'
import { StatsOverview } from './components/StatsRail'
import { useDashboardData } from './hooks/useDashboardData'
import { usePollMs } from './hooks/usePageVisible'
import { useStickyTab } from './hooks/useStickyTab'
import { api } from './lib/api'
import type { DateRange, PeriodKey } from './types/analytics'

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
const DayReviewPanel = lazy(() =>
  import('./components/DayReviewPanel').then((m) => ({ default: m.DayReviewPanel })),
)
const CryptoPanel = lazy(() =>
  import('./components/CryptoPanel').then((m) => ({ default: m.CryptoPanel })),
)
const FuturesPanel = lazy(() =>
  import('./components/FuturesPanel').then((m) => ({ default: m.FuturesPanel })),
)
const CommoditiesPanel = lazy(() =>
  import('./components/CommoditiesPanel').then((m) => ({ default: m.CommoditiesPanel })),
)
const OperationsPanel = lazy(() =>
  import('./components/OperationsPanel').then((m) => ({ default: m.OperationsPanel })),
)
const ReportsPage = lazy(() =>
  import('./components/pages/ReportsPage').then((m) => ({ default: m.ReportsPage })),
)
const StrategyPerformancePage = lazy(() =>
  import('./components/pages/StrategyPerformancePage').then((m) => ({
    default: m.StrategyPerformancePage,
  })),
)
const TradeHistoryPage = lazy(() =>
  import('./components/pages/TradeHistoryPage').then((m) => ({ default: m.TradeHistoryPage })),
)
const StrategiesPage = lazy(() =>
  import('./components/strategies/StrategiesPage').then((m) => ({ default: m.StrategiesPage })),
)

type StatusResponse = {
  trading_mode: string
  kill_switch?: { active?: boolean; reasons?: string[] }
  policy?: { lots_per_trade?: number; order_quantities?: Record<string, number> }
  market?: { message?: string; now_ist?: string; is_open?: boolean; phase?: string }
  strategy?: Record<string, unknown>
  dhan_ready?: boolean
  live_allowed?: boolean
  trading_gates?: {
    can_send_live_orders?: boolean
    trading_mode?: string
    reasons?: Array<{ title?: string; detail?: string }>
  }
  auto?: { running?: boolean; last_error?: string }
  ui?: { broker_account?: boolean; strategy_builder?: boolean; strategy_internals?: boolean }
}

function PanelFallback() {
  return <p className="text-xs text-slate-500">Loading…</p>
}

function greeting(): string {
  const h = Number(
    new Intl.DateTimeFormat('en-US', {
      timeZone: 'Asia/Kolkata',
      hour: 'numeric',
      hour12: false,
    }).format(new Date()),
  )
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

function App() {
  const [period, setPeriod] = useState<PeriodKey>('today')
  const [range, setRange] = useState<DateRange>({ from: '', to: '' })
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
  const marketMsg = status.data?.market?.message
  const marketOpen = status.data?.market?.is_open
  const phaseLabel =
    marketMsg ||
    (marketOpen
      ? 'Market open'
      : status.data?.market?.phase === 'square_off'
        ? 'Square-off'
        : 'Market closed')

  const refreshLabel = [
    dashboard.statsUpdatedAt ? `Stats ${dashboard.statsUpdatedAt}` : '',
    dashboard.hasOpen && dashboard.mtmUpdatedAt ? `MTM ${dashboard.mtmUpdatedAt}` : '',
    dashboard.isFetching ? 'syncing…' : '',
  ]
    .filter(Boolean)
    .join(' · ')

  const nav: NavGroup[] = [
    {
      items: [
        { id: 'trade', label: 'Dashboard', icon: <IconGrid />, badge: openCount || null },
        { id: 'strategies', label: 'Strategies', icon: <IconLayers /> },
        { id: 'crypto', label: 'Crypto', icon: <IconCoin /> },
        { id: 'futures', label: 'Futures', icon: <IconChart /> },
        { id: 'commodities', label: 'Commodities', icon: <IconChart /> },
      ],
    },
    {
      label: 'Insights',
      items: [
        { id: 'performance', label: 'Strategy P&L', icon: <IconLayers /> },
        { id: 'trades', label: 'Trade history', icon: <IconList /> },
        { id: 'reports', label: 'Reports & PnL', icon: <IconChart /> },
      ],
    },
    {
      label: 'More',
      items: [
        { id: 'research', label: 'Research', icon: <IconFlask /> },
        { id: 'setup', label: 'Settings', icon: <IconGear /> },
      ],
    },
  ]

  return (
    <AppShell
      nav={nav}
      active={tab}
      onNavigate={setTab}
      topRight={
        <StatusPills
          tradingMode={status.data?.trading_mode}
          market={status.data?.market}
          dhanReady={status.data?.dhan_ready}
        />
      }
    >
      {ks?.active ? (
        <p className="mb-4 rounded-lg border border-[var(--armed)]/40 bg-[var(--armed)]/10 px-3 py-2 text-sm text-[var(--armed)]">
          Kill switch active: {(ks.reasons || []).join(' ')}
        </p>
      ) : null}
      {dashboard.isError ? (
        <p className="mb-4 rounded-lg border border-[var(--armed)]/40 bg-[var(--armed)]/10 px-3 py-2 text-sm text-[var(--armed)]">
          {dashboard.error instanceof Error ? dashboard.error.message : 'Failed to load analytics'}
        </p>
      ) : null}

      {tab === 'trade' ? (
        <>
          <PageHeader
            eyebrow="Overview · NIFTY · BANKNIFTY · SENSEX"
            title={greeting()}
            status={`${phaseLabel} · CPR + EMA + OI${refreshLabel ? ` · ${refreshLabel}` : ''}`}
          />
          <StatsOverview
            period={period}
            onPeriodChange={setPeriod}
            range={range}
            onRangeChange={setRange}
            updatedLabel={refreshLabel}
            marketMessage={marketMsg}
            marketOpen={marketOpen}
            analytics={dashboard.analytics}
            trades={dashboard.trades}
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
              range={range}
              mtmUpdatedAt={dashboard.mtmUpdatedAt}
            />
            <aside className="space-y-4">
              <CollapsibleSection title="Auto trader & scanner" defaultOpen>
                <Suspense fallback={<PanelFallback />}>
                  <AutoTraderPanel />
                </Suspense>
              </CollapsibleSection>
              {status.data?.ui?.broker_account !== false ? (
                <CollapsibleSection title="Dhan broker account" defaultOpen>
                  <Suspense fallback={<PanelFallback />}>
                    <DhanAccountPanel tradingMode={status.data?.trading_mode} />
                  </Suspense>
                </CollapsibleSection>
              ) : null}
            </aside>
          </div>
        </>
      ) : null}

      {tab === 'strategies' ? (
        <>
          <PageHeader
            eyebrow="Strategies"
            title="Strategy catalog"
            status="Your strategies, the builder, the marketplace, exchanges and deployments."
          />
          <Suspense fallback={<PanelFallback />}>
            <StrategiesPage
              showBuilder={status.data?.ui?.strategy_builder !== false}
              showInternals={status.data?.ui?.strategy_internals !== false}
            />
          </Suspense>
          {status.data?.ui?.strategy_internals !== false ? (
            <div className="mt-8">
              <CollapsibleSection title="AI brain & ML gate">
                <Suspense fallback={<PanelFallback />}>
                  <BrainPanel />
                </Suspense>
              </CollapsibleSection>
            </div>
          ) : null}
        </>
      ) : null}

      {tab === 'research' ? (
        <>
          <PageHeader
            eyebrow="Research"
            title="Backtest & learning"
            status="Replay the signal over Dhan history; learn from closed outcomes."
          />
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
        </>
      ) : null}

      {tab === 'crypto' ? (
        <>
          <PageHeader
            eyebrow="Crypto · Delta Exchange"
            title="Crypto"
            status="Perpetual futures on Delta Exchange India — 24/7, its own ML."
          />
          <CollapsibleSection title="Crypto — Delta Exchange" defaultOpen>
            <Suspense fallback={<PanelFallback />}>
              <CryptoPanel />
            </Suspense>
          </CollapsibleSection>
        </>
      ) : null}

      {tab === 'futures' ? (
        <>
          <PageHeader
            eyebrow="Futures"
            title="Futures"
            status="Directional index-futures paper lane + the stock-futures replay verdict."
          />
          <Suspense fallback={<PanelFallback />}>
            <FuturesPanel />
          </Suspense>
        </>
      ) : null}

      {tab === 'commodities' ? (
        <>
          <PageHeader
            eyebrow="Commodities · MCX"
            title="Commodities"
            status="MCX mini/micro futures — crude, gas, gold, silver — the evening the equity scanner is shut. Paper."
          />
          <Suspense fallback={<PanelFallback />}>
            <CommoditiesPanel />
          </Suspense>
        </>
      ) : null}

      {tab === 'performance' ? (
        <>
          <PageHeader
            eyebrow="Strategy P&L · per strategy · per instrument"
            title="Strategy performance"
            status="What each strategy actually earns on each instrument — gross, the Dhan/Delta charges paid, and net."
          />
          <Suspense fallback={<PanelFallback />}>
            <StrategyPerformancePage />
          </Suspense>
        </>
      ) : null}

      {tab === 'trades' ? (
        <>
          <PageHeader
            eyebrow="Trade history"
            title="Closed trades"
            status="Complete round-trip log across strategies and modes."
          />
          <Suspense fallback={<PanelFallback />}>
            <TradeHistoryPage
              logRows={dashboard.logRows}
              trades={dashboard.trades}
              mtmUpdatedAt={dashboard.mtmUpdatedAt}
            />
          </Suspense>
        </>
      ) : null}

      {tab === 'reports' ? (
        <>
          <PageHeader
            eyebrow="Reports & PnL"
            title="Performance breakdown"
            status="Equity, calendar, distribution — your own trading, measured."
          />
          <Suspense fallback={<PanelFallback />}>
            <ReportsPage analytics={dashboard.analytics} trades={dashboard.trades} />
          </Suspense>
        </>
      ) : null}

      {tab === 'setup' ? (
        <>
          <PageHeader
            eyebrow="Settings"
            title="Setup & diagnostics"
            status="Broker login, feature toggles, strategy tuning, scanner log."
          />
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
              <Suspense fallback={<PanelFallback />}>
                <OperationsPanel />
              </Suspense>
            </CollapsibleSection>
          </div>
        </>
      ) : null}
    </AppShell>
  )
}

export default App
