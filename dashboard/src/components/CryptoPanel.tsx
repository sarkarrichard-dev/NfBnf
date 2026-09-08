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
import { CollapsibleSection } from './CollapsibleSection'
import { PeriodBar } from './PeriodBar'
import { Sparkline } from './Sparkline'
import { CryptoSetupPanel } from './CryptoSetupPanel'
import { CryptoExecutionPanel } from './CryptoExecutionPanel'
import { CryptoDayReviewPanel } from './CryptoDayReviewPanel'

type Status = {
  lanes: { ny_n_break: boolean; ichimoku: boolean }
  sizing: { lots: number; deploy_cap_usd: number; leverage: number; max_concurrent: number }
  trailing: {
    stop_pnl_pct: number
    ratchet_step_pnl_pct: number
    tp_trigger_pnl_pct: number
    peak_trail_pnl_pct: number
  }
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
  source?: string
  note?: string
}
type Lots = { lots: number; table: LotRow[] }
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
  live: unknown[]
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

/** Cumulative realised USD P&L, in row order, for the equity sparkline. */
function equityCurve(rows: { pnl_usd: number }[]): number[] {
  const out: number[] = []
  for (const r of rows) out.push((out[out.length - 1] ?? 0) + (Number(r.pnl_usd) || 0))
  return out
}

function StatTile({
  label,
  value,
  sub,
  valueCls,
  points,
}: {
  label: string
  value: string
  sub?: string
  valueCls?: string
  points?: number[]
}) {
  return (
    <article className={fx.card}>
      <p className={fx.cardLabel}>{label}</p>
      <p className={cn(fx.cardValue, valueCls || 'text-slate-100')}>{value}</p>
      {sub ? <p className="mt-0.5 text-[11px] text-slate-500 tabular-nums">{sub}</p> : null}
      {points && points.length > 1 ? (
        <Sparkline points={points} className="mt-1 w-full" height={14} />
      ) : null}
    </article>
  )
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

  const allTrades = journal.data?.trades ?? []
  const rows = cryptoRowsForPeriod(allTrades, period, range)
  const st = cryptoStats(rows)
  const equity = equityCurve(rows)
  const openMtmUsd = positions.data?.open_unrealized_usd ?? 0
  const openMtmInr = positions.data?.open_unrealized_inr ?? 0

  const allSymbols = [...new Set([...(s?.available_symbols ?? []), ...(s?.symbols ?? [])])].sort()

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
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
          <StatTile label="Realized ($)" value={usd(st.net_usd)} valueCls={pnlCls(st.net_usd)} />
          <StatTile label="Realized (₹)" value={inr(st.net_inr)} valueCls={pnlCls(st.net_inr)} />
          <StatTile
            label="Open MTM"
            value={usd(openMtmUsd)}
            valueCls={pnlCls(openMtmUsd)}
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
          <span className="text-[11px] text-slate-600">both off = paused</span>
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
            {' · '}
            {s?.nbreak_allround
              ? 'N-Break 24/7'
              : `N-Break window ${s?.session_ist.start}–${s?.session_ist.end} IST`}
          </p>
        ) : null}

        {/* minimum capital per instrument */}
        <div className="space-y-1.5">
          <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
            Minimum capital per instrument · {s?.sizing.lots ?? 1} lot
            {(s?.sizing.lots ?? 1) === 1 ? '' : 's'} @ 100x
          </span>
          <LotTable rows={lotsQ.data?.table ?? []} lots={lotsQ.data?.lots ?? s?.sizing.lots ?? 1} />
        </div>
      </div>

      {/* open positions */}
      <section className={cn(fx.panel, 'p-4')}>
        <h3 className="mb-3 text-sm font-semibold text-cyan-50/95">Open (paper)</h3>
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
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-cyan-200/45">No open paper positions.</p>
        )}
      </section>

      {/* trade history — the selected period / range */}
      <section className={cn(fx.panel, 'p-4')}>
        <h3 className="mb-3 text-sm font-semibold text-cyan-50/95">
          Trade history <span className="text-cyan-200/45">({rows.length})</span>
        </h3>
        {rows.length ? (
          <div className="max-h-[28rem] overflow-auto rounded-lg border border-[var(--hair)] bg-black/25">
            <table className="min-w-full text-xs">
              <thead className="sticky top-0 z-10 bg-slate-950/95 text-cyan-200/50">
                <tr className="border-b border-slate-800 [&>th]:px-3 [&>th]:py-2 [&>th]:text-left [&>th]:font-medium">
                  <th>Day</th>
                  <th>Asset</th>
                  <th>Strategy</th>
                  <th>Side</th>
                  <th>Entry → Exit</th>
                  <th>P&L</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {[...rows].reverse().map((t, i) => (
                  <tr
                    key={i}
                    className="border-b border-slate-800/60 text-slate-200 [&>td]:px-3 [&>td]:py-2"
                  >
                    <td className="text-slate-500">{t.day}</td>
                    <td>{t.asset}</td>
                    <td className="text-slate-400">{t.strategy}</td>
                    <td className={t.side === 'long' ? 'text-[var(--up)]' : 'text-[var(--down)]'}>
                      {t.side}
                    </td>
                    <td className="text-slate-400 tabular-nums">
                      ${num(t.entry_price)} → ${num(t.exit_price)}
                    </td>
                    <td className={cn('tabular-nums', pnlCls(t.pnl_usd))}>
                      {usd(t.pnl_usd)} <span className="text-slate-600">{inr(t.pnl_inr)}</span>
                    </td>
                    <td className="text-slate-500">{t.exit_reason ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-cyan-200/45">
            {allTrades.length ? 'No trades in this period.' : 'No closed trades yet.'}
          </p>
        )}
      </section>

      {s?.ml ? <LearningRow ml={s.ml} /> : null}

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
      <div className="absolute z-20 mt-1 w-72 max-w-full rounded-lg border border-[var(--hair)] bg-[var(--panel)] p-2 shadow-lg">
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
        {ok(ml.oos_delta_usd) ? ` · OOS Δ ${usd(ml.oos_delta_usd)}` : ''}
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
                  (OOS {usd(v.net_usd ?? 0)})
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

function LotTable({ rows, lots }: { rows: LotRow[]; lots: number }) {
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
            <th>Notional / lot</th>
            <th>
              Min capital ({lots} lot{lots === 1 ? '' : 's'})
            </th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {rows.map((r) => {
            const minUsd = ok(r.margin_per_lot_usd) ? r.margin_per_lot_usd * lots : null
            return (
              <tr
                key={r.symbol}
                className="border-b border-slate-800/60 text-slate-200 [&>td]:px-3 [&>td]:py-2"
              >
                <td>{r.symbol}</td>
                <td className="tabular-nums text-slate-400">
                  {ok(r.coin_per_lot) ? `${r.coin_per_lot} ${r.symbol.replace(/USD.?$/, '')}` : '—'}
                </td>
                <td className="tabular-nums text-slate-400">{usd0(r.notional_per_lot_usd)}</td>
                <td className="tabular-nums">
                  {ok(minUsd) ? (
                    <>
                      {usd0(minUsd)}{' '}
                      <span className="text-slate-600">{inr0(usdInr ? minUsd * usdInr : null)}</span>
                      {r.source === 'delta' ? <span className="text-slate-600"> · Delta</span> : null}
                    </>
                  ) : (
                    <span className="text-[var(--warn)]/80">— · {r.note || 'no mark'}</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
