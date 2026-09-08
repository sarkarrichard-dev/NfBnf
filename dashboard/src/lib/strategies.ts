/** The strategy catalog.
 *
 *  The set of strategies is fixed and small (three crypto engines + the two
 *  index lanes), so it lives here rather than behind an endpoint. Each entry
 *  mirrors the frozen config dataclass in `crypto/strategies/*.py` /
 *  `index_ai/` so the Builder can render a parameter form and the "READS"
 *  sentence without a round-trip. Live status is layered on at render time
 *  from `/api/{futures,options-cpr,crypto}/status`.
 *
 *  When Phase 4 adds per-user custom strategies, this becomes the seed list
 *  and saved permutations come from the user store.
 */

export type ParamGroup = 'market' | 'signal' | 'risk'

export type ParamDef = {
  key: string
  label: string
  group: ParamGroup
  type: 'int' | 'float' | 'bool' | 'select'
  default: number | boolean | string
  min?: number
  max?: number
  step?: number
  options?: string[]
  hint?: string
}

export type StrategyDef = {
  id: string
  name: string
  kind: 'crypto' | 'index'
  /** which lane / status block reports it */
  statusKey: 'ny_n_break' | 'ichimoku' | 'candle_renko' | 'options_cpr' | 'futures'
  engine: string
  instrument: string
  timeframe: string
  blurb: string
  /** plain-English template; {tokens} are param keys */
  reads: string
  /** backtest headline, already measured — see crypto/strategies/RESULTS.md */
  backtest?: { window: string; net: string; trades: number; note?: string }
  /** default-on in the paper lane? */
  paperDefault: boolean
  /** exposed in the Builder (index lanes tune via .env, not here) */
  builder: boolean
  params: ParamDef[]
}

const TRAIL: ParamDef[] = [
  { key: 'stop_pnl_pct', label: 'Initial stop', group: 'risk', type: 'float', default: 10, min: 2, max: 40, step: 1, hint: '% of P&L on margin — price-based stops are meaningless at 100×' },
  { key: 'ratchet_step_pnl_pct', label: 'Ratchet step', group: 'risk', type: 'float', default: 5, min: 1, max: 20, step: 1, hint: 'every N% of peak P&L lifts the stop by N%' },
  { key: 'tp_trigger_pnl_pct', label: 'Profit-floor trigger', group: 'risk', type: 'float', default: 25, min: 5, max: 100, step: 5 },
  { key: 'peak_trail_pnl_pct', label: 'Peak trail', group: 'risk', type: 'float', default: 2, min: 1, max: 10, step: 1 },
]

export const STRATEGIES: StrategyDef[] = [
  {
    id: 'ny_n_break',
    name: 'NY N-Break',
    kind: 'crypto',
    statusKey: 'ny_n_break',
    engine: 'EMA25 + VWAP bias · first-swing re-break',
    instrument: 'BTC / ETH perp',
    timeframe: '5m entry · 15m exit',
    blurb:
      'The "6 PM" strategy. Trades only inside the New York session (18:00–23:00 IST). Bias needs price on one side of both EMA25 and VWAP; entry is a closing-basis re-break of the first swing after a retrace — the "N" shape. Exit watches the 15m structure.',
    reads:
      'In the NY session, when price closes above both EMA{ema_len} and VWAP, arm on the first swing high; enter long when it re-breaks. Up to {max_trades_per_session} trades/session. Trail the stop {stop_pnl_pct}% on margin.',
    backtest: { window: 'paper since 2026-09', net: 'paper lane', trades: 0, note: 'live paper — no backtest replay (session-scoped)' },
    paperDefault: true,
    builder: true,
    params: [
      { key: 'ema_len', label: 'EMA length', group: 'market', type: 'int', default: 25, min: 8, max: 100 },
      { key: 'swing_left', label: 'Swing left bars', group: 'signal', type: 'int', default: 5, min: 2, max: 15 },
      { key: 'swing_right', label: 'Swing right bars', group: 'signal', type: 'int', default: 2, min: 1, max: 10 },
      { key: 'exit_swing_left', label: 'Exit swing left', group: 'signal', type: 'int', default: 5, min: 2, max: 15 },
      { key: 'exit_swing_right', label: 'Exit swing right', group: 'signal', type: 'int', default: 2, min: 1, max: 10 },
      { key: 'max_trades_per_session', label: 'Max trades / session', group: 'risk', type: 'int', default: 3, min: 1, max: 10 },
      ...TRAIL,
    ],
  },
  {
    id: 'ichimoku',
    name: 'Ichimoku Cloud',
    kind: 'crypto',
    statusKey: 'ichimoku',
    engine: 'Tenkan/Kijun cross · cloud filter',
    instrument: 'BTC / ETH perp',
    timeframe: '15m',
    blurb:
      'Classic Ichimoku trend-following. Long when the conversion line crosses above the base line with price above the cloud; mirrored for shorts. The shared P&L trailing engine handles the exit.',
    reads:
      'On the 15m frame, go long when Tenkan({conversion}) crosses above Kijun({base}) and price is above the {span_b}-period cloud. Trail the stop {stop_pnl_pct}% on margin.',
    backtest: { window: '2026 candle history', net: 'dormant', trades: 0, note: 'kept as an indicator lane; not enabled live' },
    paperDefault: false,
    builder: true,
    params: [
      { key: 'conversion', label: 'Conversion (Tenkan)', group: 'signal', type: 'int', default: 9, min: 5, max: 30 },
      { key: 'base', label: 'Base (Kijun)', group: 'signal', type: 'int', default: 26, min: 10, max: 60 },
      { key: 'span_b', label: 'Span B', group: 'signal', type: 'int', default: 52, min: 20, max: 120 },
      { key: 'displacement', label: 'Displacement', group: 'market', type: 'int', default: 26, min: 10, max: 60 },
      ...TRAIL,
    ],
  },
  {
    id: 'candle_renko',
    name: 'Candle-Renko',
    kind: 'crypto',
    statusKey: 'candle_renko',
    engine: '5m reversal bar · 15m Supertrend · Renko gate',
    instrument: 'BTC / ETH perp',
    timeframe: '5m entry · 15m trend',
    blurb:
      "Richard's own spec. A 5-minute candlestick reversal bar (engulfing / hammer / shooting-star), only in the direction of the 15-minute Supertrend, and only when the last ATR-sized Renko brick agrees. Exit on the P&L trailing engine or a 15m Supertrend flip.",
    reads:
      'Enter long on a bullish 5m reversal bar when 15m Supertrend({st_period}, {st_mult}) is up and the last {renko_atr_mult}×ATR({atr_len}) Renko brick is green. Exit on the trail or a Supertrend flip.',
    backtest: { window: '90 days, BTC/ETH', net: 'see RESULTS.md', trades: 0, note: 'auto-tuned nightly; enabled in paper' },
    paperDefault: true,
    builder: true,
    params: [
      { key: 'atr_len', label: 'ATR length', group: 'market', type: 'int', default: 14, min: 5, max: 40 },
      { key: 'renko_atr_mult', label: 'Renko brick × ATR', group: 'signal', type: 'float', default: 1.0, min: 0.5, max: 3, step: 0.25 },
      { key: 'st_period', label: 'Supertrend period', group: 'signal', type: 'int', default: 10, min: 5, max: 30 },
      { key: 'st_mult', label: 'Supertrend multiplier', group: 'signal', type: 'float', default: 3.0, min: 1, max: 6, step: 0.5 },
      ...TRAIL,
    ],
  },
  {
    id: 'options_cpr',
    name: 'Options CPR',
    kind: 'index',
    statusKey: 'options_cpr',
    engine: 'CPR + EMA + Supertrend + OI',
    instrument: 'NIFTY · BANKNIFTY · SENSEX',
    timeframe: '5m',
    blurb:
      'The index options lane — naked single-leg buying and directional selling off a CPR + EMA + Supertrend signal with an OI confirm. Gated by the measured cost floor per structure (viability.py); BANKNIFTY 4-leg structures stay blocked.',
    reads: 'Tuned through Settings → Strategy tuning (.env), not the Builder.',
    backtest: { window: 'full history', net: 'net-negative after costs', trades: 0, note: 'friction is the binding constraint — see strategy-findings' },
    paperDefault: true,
    builder: false,
    params: [],
  },
  {
    id: 'futures',
    name: 'Index Futures',
    kind: 'index',
    statusKey: 'futures',
    engine: 'CPR + EMA + Supertrend',
    instrument: 'NIFTY · BANKNIFTY · SENSEX',
    timeframe: '5m',
    blurb:
      'The index futures paper lane — the same directional signal as the options lane, expressed as an outright futures position. Paper only.',
    reads: 'Tuned through Settings → Strategy tuning (.env), not the Builder.',
    paperDefault: true,
    builder: false,
    params: [],
  },
]

export const byId = (id: string) => STRATEGIES.find((s) => s.id === id)

/** Fill {tokens} in a `reads` template from a param-value map. */
export function renderReads(tpl: string, values: Record<string, number | boolean | string>): string {
  return tpl.replace(/\{(\w+)\}/g, (_, k) => String(values[k] ?? `{${k}}`))
}
