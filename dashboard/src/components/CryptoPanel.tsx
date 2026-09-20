import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { fx } from '../lib/theme'
import {
  cryptoRowsForPeriod,
  cryptoStats,
  inr,
  inr0,
  num,
  ok,
  pnlCls,
  usd,
  usd0,
} from '../lib/cryptoFmt'
import type { DateRange, PeriodKey } from '../types/analytics'
import { Button } from './ui/Button'
import { StatTile } from './ui/StatTile'
import { CollapsibleSection } from './CollapsibleSection'
import { PeriodBar } from './PeriodBar'
import { Sparkline } from './Sparkline'
import { CryptoSetupPanel } from './CryptoSetupPanel'
import { CryptoExecutionPanel } from './CryptoExecutionPanel'
import { CryptoDayReviewPanel } from './CryptoDayReviewPanel'
import { TradeLogTable } from './TradeLogTable'
import { cryptoToLogRows, cryptoToTradeRows } from '../lib/cryptoRows'

type Status = {
  lanes: {
    ny_n_break: boolean
    ichimoku: boolean
    ak_roxx_pro: boolean
    cpr_trend: boolean
  }
  sizing: {
    margin_per_position_usd: number
    deploy_cap_usd: number
    leverage: number
    max_concurrent: number
  }
  trailing: {
    stop_pnl_pct: number
    ratchet_step_pnl_pct: number
    tp_trigger_pnl_pct: number
    peak_trail_pnl_pct: number
  }
  lane_session_ist: { start: string; end: string }
  session_ist: { start: string; end: string }
  nbreak_allround?: boolean
  ichimoku_tf: string
  symbols: string[]
  available_symbols: string[]
  ml: {
    enabled: boolean
    rows: number
    live_rows: number
    gate_armed: boolean
    min_win_prob_gate: number
    oos_delta_usd: number | null
    model_present: boolean
    min_rows: number
    min_live_rows: number
    tuning?: Record<
      string,
      { params?: Record<string, number>; net_usd?: number | null; stable?: boolean; tuned_at?: string }
    >
  }
}
type LotRow = {
  symbol: string
  coin_per_lot: number | null
  notional_per_lot_usd: number | null
  margin_per_lot_usd: number | null
  margin_per_lot_inr: number | null
  lots: number | null
  deployed_usd: number | null
  source?: string
  note?: string
}
type Lots = { margin_per_position_usd: number; table: LotRow[] }
type PaperPos = {
  key: string
  asset: string
  strategy: string
  side: string
  size: number
  entry_price: number
  leverage: number
  margin_total_usd: number
  mark: number
  unrealized_usd: number
  unrealized_inr: number
  unrealized_pct: number
}
type Positions = {
  paper: PaperPos[]
  open_unrealized_usd: number
  open_unrealized_inr: number
}
type Trade = {
  day: string
  closed_at?: string
  exit_time?: string
  strategy: string
  asset: string
  side: string
  size: number
  entry_price: number
  exit_price: number
  pnl_usd: number
  pnl_inr: number
  exit_reason?: string
}

type LiveReadiness = {
  strategy: string
  trades: number
  trades_needed: number
  days_span: number
  days_needed: number
  net_usd: number
  instruments_positive: number
  instruments_total: number
  ready: boolean
  why_not: string | null
}

/** Cumulative realised USD P&L, in row order, for the equity sparkline. */
function equityCurve(rows: { pnl_usd: number }[]): number[] {
  const out: number[] = []
  for (const r of rows) out.push((out[out.length - 1] ?? 0) + (Number(r.pnl_usd) || 0))
  return out
}

export function CryptoPanel() {
  const qc = useQueryClient()
  const [period, setPeriod] = useState<PeriodKey>('today')
  const [range, setRange] = useState<DateRange>({ from: '', to: '' })

  const status = useQuery({
    queryKey: ['crypto', 'status'],
    queryFn: () => api<Status>('/api/crypto/status'),
  })
  const positions = useQuery({
    queryKey: ['crypto', 'positions'],
    queryFn: () => api<Positions>('/api/crypto/positions'),
    refetchInterval: 20_000,
  })
  const journal = useQuery({
    queryKey: ['crypto', 'journal'],
    queryFn: () => api<{ trades: Trade[] }>('/api/crypto/journal?limit=500'),
    refetchInterval: 60_000,
  })
  const lotsQ = useQuery({
    queryKey: ['crypto', 'lots'],
    queryFn: () => api<Lots>('/api/crypto/lots'),
    refetchInterval: 60_000,
  })
  const readiness = useQuery({
    queryKey: ['crypto', 'live-readiness'],
    queryFn: () => api<{ strategies: LiveReadiness[] }>('/api/crypto/live-readiness'),
    refetchInterval: 5 * 60_000,
  })

  const s = status.data

  const cfg = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api('/api/crypto/config', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      toast.success('Saved — restart the server to apply')
      void qc.invalidateQueries({ queryKey: ['crypto'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const closePos = useMutation({
    mutationFn: (key: string) =>
      api<{ ok: boolean; error?: string; trade?: { pnl_usd: number; pnl_inr: number } }>(
        '/api/crypto/positions/close',
        { method: 'POST', body: JSON.stringify({ key }) },
      ),
    onSuccess: (res) => {
      if (!res.ok) {
        toast.error(res.error || 'Close failed')
        return
      }
      const t = res.trade
      toast.success(t ? `Closed — ${usd(t.pnl_usd)} (${inr(t.pnl_inr)})` : 'Closed')
      void qc.invalidateQueries({ queryKey: ['crypto', 'positions'] })
      void qc.invalidateQueries({ queryKey: ['crypto', 'journal'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const closeAllPos = useMutation({
    mutationFn: () =>
      api<{ ok: boolean; attempted: number; closed: string[]; failed: Record<string, string> }>(
        '/api/crypto/positions/close-all',
        { method: 'POST' },
      ),
    onSuccess: (res) => {
      if (res.attempted === 0) {
        toast.success('Nothing open to close')
      } else if (res.ok) {
        toast.success(`Closed all ${res.closed.length} open position${res.closed.length === 1 ? '' : 's'}`)
      } else {
        const failedCount = Object.keys(res.failed).length
        toast.error(`Closed ${res.closed.length}, ${failedCount} failed — see server log`)
      }
      void qc.invalidateQueries({ queryKey: ['crypto', 'positions'] })
      void qc.invalidateQueries({ queryKey: ['crypto', 'journal'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const allTrades = journal.data?.trades ?? []
  const rows = cryptoRowsForPeriod(allTrades, period, range)
  const st = cryptoStats(rows)
  const equity = equityCurve(rows)
  const openMtmUsd = positions.data?.open_unrealized_usd ?? 0
  const openMtmInr = positions.data?.open_unrealized_inr ?? 0

  const allSymbols = [...new Set([...(s?.available_symbols ?? []), ...(s?.symbols ?? [])])].sort()

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-400">
        Delta Exchange India · perpetual futures. Paper by default; the Mode switch below arms
        real orders.
      </p>

      <CryptoExecutionPanel />

      {/* stats rail — same shape as the index tab */}
      <section className={cn(fx.panel, 'px-3 py-2.5')}>
        <div className="mb-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <PeriodBar
            period={period}
            onPeriodChange={setPeriod}
            range={range}
            onRangeChange={setRange}
          />
          {equity.length > 1 ? (
            <div className="flex items-center gap-2" title="Cumulative realised P&L, this period">
              <span className="text-[11px] text-slate-500">Equity</span>
              <Sparkline points={equity} width={120} height={26} />
              <span className={cn('text-xs font-semibold tabular-nums', pnlCls(equity[equity.length - 1]))}>
                {usd(equity[equity.length - 1])}
              </span>
            </div>
          ) : null}
        </div>
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-6">
          <StatTile label="Trades" value={String(st.trades)} />
          <StatTile label="Wins / Losses" value={`${st.wins} / ${st.losses}`} />
          <StatTile
            label="Win rate"
            value={st.win_rate == null ? '—' : `${(st.win_rate * 100).toFixed(1)}%`}
          />
          <StatTile label="Realized ($)" value={usd(st.net_usd)} valueClass={pnlCls(st.net_usd)} />
          <StatTile label="Realized (₹)" value={inr(st.net_inr)} valueClass={pnlCls(st.net_inr)} />
          <StatTile
            label="Open MTM"
            value={usd(openMtmUsd)}
            valueClass={pnlCls(openMtmUsd)}
            sub={`${inr(openMtmInr)} · ${positions.data?.paper?.length ?? 0} pos`}
          />
        </div>
      </section>

      {/* controls */}
      <div className={cn(fx.panel, 'space-y-3 p-4')}>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-cyan-50/90">Strategies</span>
          <Chip
            label="6 PM (NY N-Break)"
            on={!!s?.lanes.ny_n_break}
            onClick={() => cfg.mutate({ ny_n_break_enabled: !s?.lanes.ny_n_break })}
          />
          <Chip
            label="Ichimoku"
            on={!!s?.lanes.ichimoku}
            onClick={() => cfg.mutate({ ichimoku_enabled: !s?.lanes.ichimoku })}
          />
          <Chip
            label="AK Roxx Pro"
            on={!!s?.lanes.ak_roxx_pro}
            onClick={() => cfg.mutate({ ak_roxx_enabled: !s?.lanes.ak_roxx_pro })}
          />
          <Chip
            label="CPR Trend"
            on={!!s?.lanes.cpr_trend}
            onClick={() => cfg.mutate({ cpr_trend_enabled: !s?.lanes.cpr_trend })}
          />
          <span className="text-[11px] text-slate-400">all off = paused</span>
        </div>

        {/* symbol picker */}
        <div className="space-y-1.5">
          <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
            Trade symbols
          </span>
          <SymbolSelect
            all={allSymbols}
            picked={s?.symbols ?? []}
            pending={cfg.isPending}
            onApply={(next) => cfg.mutate({ symbols: next })}
          />
          {(s?.symbols?.length ?? 0) > 4 ? (
            <p className="text-[11px] text-[var(--warn)]/80">
              {s?.symbols.length} symbols × ~5 Delta calls per 60s scan — within Delta's quota,
              but trims the scan headroom.
            </p>
          ) : null}
        </div>

        {/* exit rule — read-only */}
        {s?.trailing ? (
          <p className="text-[11px] text-slate-500">
            Exit: stop −{s.trailing.stop_pnl_pct}% P&L, ratchets +{s.trailing.ratchet_step_pnl_pct}%
            for every +{s.trailing.ratchet_step_pnl_pct}% gained · trailing profit from +
            {s.trailing.tp_trigger_pnl_pct}%, then floor tracks peak −{s.trailing.peak_trail_pnl_pct}%
            {s?.lane_session_ist
              ? ` · Entries ${s.lane_session_ist.start}–${s.lane_session_ist.end} IST`
              : ''}
            {s?.nbreak_allround
              ? ' · N-Break 24/7'
              : ` · N-Break ${s?.session_ist.start}–${s?.session_ist.end}`}
          </p>
        ) : null}

        {/* what the $ budget buys per instrument */}
        <div className="space-y-1.5">
          <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
            {usd0(lotsQ.data?.margin_per_position_usd ?? s?.sizing.margin_per_position_usd ?? 0)} per
            position · auto-sized @ {s?.sizing.leverage ?? 20}x
          </span>
          <LotTable rows={lotsQ.data?.table ?? []} />
        </div>
      </div>

      {/* open positions */}
      <section className={cn(fx.panel, 'p-4')}>
        <div className="mb-3 flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-cyan-50/95">Open (paper)</h3>
          {positions.data?.paper?.length ? (
            <button
              type="button"
              disabled={closeAllPos.isPending}
              onClick={() => {
                const n = positions.data?.paper?.length ?? 0
                if (!window.confirm(`Close all ${n} open position${n === 1 ? '' : 's'} now, at current prices?`))
                  return
                closeAllPos.mutate()
              }}
              className="rounded-md border border-[var(--down)]/40 px-2 py-1 font-sans text-[11px] font-semibold text-[var(--down)] transition hover:bg-[var(--down)]/10 disabled:opacity-40"
            >
              {closeAllPos.isPending ? 'Closing all…' : 'Close all'}
            </button>
          ) : null}
        </div>
        {positions.data?.paper?.length ? (
          <div className="overflow-x-auto rounded-lg border border-[var(--hair)] bg-black/25">
            <table className="min-w-full text-xs">
              <thead className="text-cyan-200/50">
                <tr className="border-b border-slate-800 [&>th]:px-3 [&>th]:py-2 [&>th]:text-left [&>th]:font-medium">
                  <th>Asset</th>
                  <th>Strategy</th>
                  <th>Side</th>
                  <th>Size</th>
                  <th>Entry → Mark</th>
                  <th>Unrealised P&L</th>
                  <th>Margin</th>
                  <th></th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {positions.data.paper.map((p) => (
                  <tr
                    key={p.key}
                    className="border-b border-slate-800/60 text-slate-200 [&>td]:px-3 [&>td]:py-2"
                  >
                    <td>{p.asset}</td>
                    <td className="text-slate-400">{p.strategy}</td>
                    <td className={p.side === 'long' ? 'text-[var(--up)]' : 'text-[var(--down)]'}>
                      {p.side}
                    </td>
                    <td className="tabular-nums">{p.size}</td>
                    <td className="text-slate-400 tabular-nums">
                      ${num(p.entry_price)} → ${p.mark ? num(p.mark) : '—'}
                    </td>
                    <td className={cn('tabular-nums', pnlCls(p.unrealized_usd))}>
                      {usd(p.unrealized_usd)}{' '}
                      <span className="text-slate-600">{inr(p.unrealized_inr)}</span>
                      {ok(p.unrealized_pct) && p.unrealized_pct !== 0 ? (
                        <span className="text-slate-600">
                          {' '}
                          ({p.unrealized_pct > 0 ? '+' : ''}
                          {p.unrealized_pct}%)
                        </span>
                      ) : null}
                    </td>
                    <td className="text-slate-400 tabular-nums">
                      ${num(p.margin_total_usd)} · {num(p.leverage, 0)}x
                    </td>
                    <td>
                      <button
                        type="button"
                        disabled={closePos.isPending && closePos.variables === p.key}
                        onClick={() => {
                          if (!window.confirm(`Close ${p.asset} (${p.strategy}) now, at the current price?`)) return
                          closePos.mutate(p.key)
                        }}
                        className="rounded-md border border-[var(--down)]/40 px-2 py-1 font-sans text-[11px] font-semibold text-[var(--down)] transition hover:bg-[var(--down)]/10 disabled:opacity-40"
                      >
                        {closePos.isPending && closePos.variables === p.key ? 'Closing…' : 'Close'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-cyan-200/45">No open paper positions.</p>
        )}
      </section>

      {/* trade history — same shared table + entry/exit times as every other
          section (Index, Futures, Commodities) and the combined Trade History tab */}
      <TradeLogTable
        logRows={cryptoToLogRows(allTrades)}
        trades={cryptoToTradeRows(allTrades)}
        period={period}
        range={range}
      />

      {s?.ml ? <LearningRow ml={s.ml} /> : null}

      {readiness.data?.strategies?.length ? (
        <LiveReadinessRow strategies={readiness.data.strategies} />
      ) : null}

      <CollapsibleSection title="Day review" summary="AI summary · why each trade" defaultOpen>
        <CryptoDayReviewPanel />
      </CollapsibleSection>

      <CollapsibleSection title="Delta connection" summary="keys · wallet · live quotes">
        <CryptoSetupPanel />
      </CollapsibleSection>
    </div>
  )
}

function Chip({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full border px-3 py-1 text-xs font-medium transition',
        on
          ? 'border-[var(--acc)]/40 bg-[var(--acc)]/15 text-[var(--acc)]'
          : 'border-[var(--hair)] bg-white/[0.03] text-slate-400 hover:text-slate-200',
      )}
    >
      {label}: {on ? 'on' : 'off'}
    </button>
  )
}

/** Dropdown checkbox multi-select for the tradable perps. Local draft until Apply. */
function SymbolSelect({
  all,
  picked,
  pending,
  onApply,
}: {
  all: string[]
  picked: string[]
  pending: boolean
  onApply: (next: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<string[]>(picked)
  const key = picked.join(',')
  const [seen, setSeen] = useState(key)
  if (seen !== key) {
    setSeen(key)
    setDraft(picked)
  }
  const dirty = [...draft].sort().join(',') !== [...picked].sort().join(',')
  const toggle = (sym: string) =>
    setDraft((d) => (d.includes(sym) ? d.filter((x) => x !== sym) : [...d, sym]))

  return (
    <details
      open={open}
      onToggle={(e) => {
        setOpen(e.currentTarget.open)
        if (e.currentTarget.open) setDraft(picked)
      }}
      className="relative inline-block"
    >
      <summary className="flex w-72 max-w-full cursor-pointer list-none items-center justify-between gap-2 rounded-lg border border-[var(--hair)] bg-black/30 px-3 py-1.5 text-xs text-slate-100 [&::-webkit-details-marker]:hidden">
        <span className="truncate">
          {picked.length} symbol{picked.length === 1 ? '' : 's'}: {picked.join(', ') || '—'}
        </span>
        <span className="text-slate-500">▾</span>
      </summary>
      <div className={cn(fx.panel, 'absolute z-20 mt-1 w-72 max-w-full p-2 shadow-lg')}>
        <div className="max-h-56 space-y-0.5 overflow-auto">
          {all.map((sym) => (
            <label
              key={sym}
              className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs text-slate-200 hover:bg-white/[0.04]"
            >
              <input
                type="checkbox"
                className="accent-[var(--acc)]"
                checked={draft.includes(sym)}
                onChange={() => toggle(sym)}
              />
              {sym}
            </label>
          ))}
        </div>
        <div className="mt-2 flex items-center justify-between gap-2 border-t border-[var(--hair)] pt-2">
          <span className="text-[11px] text-slate-500">{draft.length} selected</span>
          <Button
            variant="secondary"
            pending={pending}
            disabled={!dirty || !draft.length}
            onClick={() => {
              onApply(draft)
              setOpen(false)
            }}
          >
            {draft.length ? 'Apply' : 'Pick at least one'}
          </Button>
        </div>
      </div>
    </details>
  )
}

function LearningRow({ ml }: { ml: NonNullable<Status['ml']> }) {
  const state = ml.gate_armed
    ? `gate armed @ ${(ml.min_win_prob_gate * 100).toFixed(0)}%`
    : ml.model_present
      ? 'model trained, gate not armed'
      : `collecting data — ${ml.rows}/${ml.min_rows} trades (${ml.live_rows}/${ml.min_live_rows} live)`
  return (
    <div className={cn(fx.card, 'flex flex-wrap items-center gap-x-4 gap-y-1 text-xs')}>
      <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
        Crypto learning
      </span>
      <span className="text-slate-300">{state}</span>
      <span className="text-slate-600">
        {ml.rows} trades · {ml.live_rows} live
        {ok(ml.oos_delta_usd) ? ` · held-back test edge ${usd(ml.oos_delta_usd)}` : ''}
        {ml.enabled ? '' : ' · gate off'}
      </span>
      <span className="text-slate-600">separate from the index model</span>
      {ml.tuning && Object.keys(ml.tuning).length ? (
        <div className="w-full text-[11px] text-slate-500">
          Auto-tune:{' '}
          {Object.entries(ml.tuning).map(([k, v], i) => (
            <span key={k}>
              {i > 0 ? ' · ' : ''}
              {k}{' '}
              {v.stable && (v.net_usd ?? 0) > 0 ? (
                <span className="text-[var(--up)]">
                  {Object.entries(v.params ?? {})
                    .map(([p, val]) => `${p}=${val}`)
                    .join('/')}{' '}
                  (held-back test {usd(v.net_usd ?? 0)})
                </span>
              ) : (
                <span className="text-slate-600">no stable positive combo</span>
              )}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function LiveReadinessRow({ strategies }: { strategies: LiveReadiness[] }) {
  return (
    <div className={cn(fx.card, 'space-y-2 text-xs')}>
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
          Go-live readiness
        </span>
        <span className="text-slate-600">
          real-money bar, not a calendar date — enough trades, enough days, net positive
        </span>
      </div>
      <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {strategies.map((r) => (
          <div
            key={r.strategy}
            className={cn(
              'flex flex-col gap-0.5 rounded-lg border px-3 py-1.5',
              r.ready ? 'border-[var(--up)]/35 bg-[var(--up)]/[0.06]' : 'border-[var(--hair-soft)]',
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-slate-200">{r.strategy}</span>
              <span
                className={cn(
                  'rounded-full px-2 py-0.5 text-[10px] font-semibold',
                  r.ready ? 'bg-[var(--up)]/15 text-[var(--up)]' : 'bg-white/10 text-slate-400',
                )}
              >
                {r.ready ? 'READY' : 'not yet'}
              </span>
            </div>
            <span className="text-slate-500">
              {r.trades}/{r.trades_needed} trades · {r.days_span}/{r.days_needed} days ·{' '}
              <span className={pnlCls(r.net_usd)}>{usd(r.net_usd)}</span>
            </span>
            <span className="text-slate-600">
              {r.instruments_positive}/{r.instruments_total} coins net-positive
              {r.why_not ? ` · ${r.why_not}` : ''}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function LotTable({ rows }: { rows: LotRow[] }) {
  if (!rows.length) return null
  const usdInr = (() => {
    const r = rows.find(
      (x) => ok(x.margin_per_lot_usd) && ok(x.margin_per_lot_inr) && x.margin_per_lot_usd,
    )
    return r ? (r.margin_per_lot_inr as number) / (r.margin_per_lot_usd as number) : 0
  })()
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--hair)] bg-black/25">
      <table className="min-w-full text-xs">
        <thead className="text-cyan-200/50">
          <tr className="border-b border-slate-800 [&>th]:px-3 [&>th]:py-2 [&>th]:text-left [&>th]:font-medium">
            <th>Symbol</th>
            <th>1 lot =</th>
            <th>Margin / lot</th>
            <th>Lots this buys</th>
            <th>Deployed</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {rows.map((r) => (
            <tr
              key={r.symbol}
              className="border-b border-slate-800/60 text-slate-200 [&>td]:px-3 [&>td]:py-2"
            >
              <td>{r.symbol}</td>
              <td className="tabular-nums text-slate-400">
                {ok(r.coin_per_lot) ? `${r.coin_per_lot} ${r.symbol.replace(/USD.?$/, '')}` : '—'}
              </td>
              <td className="tabular-nums text-slate-400">
                {ok(r.margin_per_lot_usd) ? (
                  <>
                    {usd0(r.margin_per_lot_usd)}{' '}
                    <span className="text-slate-600">
                      {inr0(usdInr && r.margin_per_lot_usd ? r.margin_per_lot_usd * usdInr : null)}
                    </span>
                    {r.source === 'delta' ? <span className="text-slate-600"> · Delta</span> : null}
                  </>
                ) : (
                  <span className="text-[var(--warn)]/80">— · {r.note || 'no mark'}</span>
                )}
              </td>
              <td className="tabular-nums">{r.lots ?? '—'}</td>
              <td className="tabular-nums">{ok(r.deployed_usd) ? usd0(r.deployed_usd) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
