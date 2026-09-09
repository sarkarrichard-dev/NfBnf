/** The strategy catalog.
 *
 *  The set of strategies is fixed and small (three crypto engines + the two
 *  index lanes), so it lives here rather than behind an endpoint. Each entry
 *  mirrors the frozen config dataclass in `crypto/strategies/*.py` /
 *  `index_ai/` so the Builder can render a parameter form and the "READS"
 *  sentence without a round-trip. Live status is layered on at render time
 *  from `/api/{futures,crypto}/status` (index options report via Trade History).
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
  statusKey: 'ny_n_break' | 'ichimoku' | 'fvg_scalp' | 'ema_pivot' | 'index_options' | 'futures'
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
      'The "6 PM" strategy, now around the clock. Bias needs price on one side of both EMA25 and VWAP; entry is a closing-basis re-break of the first swing after a retrace — the "N" shape. Exit watches the 15m structure or the P&L trail. Still trades the NY hours as before; the window gate is off by default (CRYPTO_NBREAK_ALLROUND).',
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
    id: 'fvg_scalp',
    name: 'FVG Scalp',
    kind: 'crypto',
    statusKey: 'fvg_scalp',
    engine: '5m fair value gap · candlestick trigger · 15m trend',
    instrument: 'BTC / ETH perp',
    timeframe: '5m entry · 15m trend',
    blurb:
      'A fast move leaves a 3-candle imbalance — a Fair Value Gap. When price retraces to retest the gap and a candlestick pattern confirms, enter: continuation when the 15m Supertrend and 5m structure agree, reversal when the trend is flat and price is stretched into an opposing gap. Filters: minimum gap width, a real impulse candle (range + volume), and an IST session window. Exit on the P&L trailing engine or a close through the gap.',
    reads:
      'When a bull FVG at least {fvg_min_atr}×ATR wide is retested with a bullish candle, enter long — continuation if 15m trend agrees, reversal if price is {stretch_atr}×ATR below EMA{ema_fast}. Only inside the {session_start_ist}:00–{session_end_ist}:00 IST window.',
    backtest: { window: '90 days, BTC/ETH/SOL', net: 'pending backtest', trades: 0, note: 'auto-tuned nightly; paper only until it clears the cost floor' },
    paperDefault: true,
    builder: true,
    params: [
      { key: 'atr_len', label: 'ATR length', group: 'market', type: 'int', default: 14, min: 5, max: 40 },
      { key: 'fvg_min_atr', label: 'Min gap width × ATR', group: 'signal', type: 'float', default: 0.25, min: 0.1, max: 1, step: 0.05 },
      { key: 'impulse_atr_mult', label: 'Impulse range × ATR', group: 'signal', type: 'float', default: 1.2, min: 0.5, max: 3, step: 0.1 },
      { key: 'impulse_vol_mult', label: 'Impulse volume ×', group: 'signal', type: 'float', default: 1.3, min: 1, max: 3, step: 0.1 },
      { key: 'st_period', label: '15m Supertrend period', group: 'signal', type: 'int', default: 10, min: 5, max: 30 },
      { key: 'st_mult', label: '15m Supertrend mult', group: 'signal', type: 'float', default: 3.0, min: 1, max: 6, step: 0.5 },
      { key: 'stretch_atr', label: 'Reversal stretch × ATR', group: 'signal', type: 'float', default: 1.5, min: 0.5, max: 4, step: 0.25 },
      { key: 'session_start_ist', label: 'Session start (IST hr)', group: 'market', type: 'int', default: 13, min: 0, max: 23 },
      { key: 'session_end_ist', label: 'Session end (IST hr)', group: 'market', type: 'int', default: 23, min: 1, max: 24 },
      ...TRAIL,
    ],
  },
  {
    id: 'ema_pivot',
    name: 'EMA + Pivot',
    kind: 'crypto',
    statusKey: 'ema_pivot',
    engine: '5m · 9/13/21 EMA fan · standard daily pivots',
    instrument: 'BTC / ETH perp',
    timeframe: '5m',
    blurb:
      'From the CoinSwitch "EMA + Pivot" video. Combines a horizontal S/R (standard daily pivots, computed from the prior UTC-day OHLC) with a dynamic one (the 9/13/21 EMA fan). Enter only when the fan is stacked and sloping in the trend direction, price is on the supporting side of the day pivot, and the last 5m close breaks a pivot level — skipping oversized trigger candles. Exit on the P&L trail, a close back through the 9 EMA, or price stretched far from it.',
    reads:
      'When the 9/13/21 EMAs are stacked & sloping up, price is above the day pivot, and a 5m close breaks a pivot level (trigger candle ≤ {big_candle_atr}×ATR), enter long. Exit on the trail, a close below EMA9, or {stretch_atr}×ATR away from EMA9.',
    backtest: { window: '90 days, BTC/ETH/SOL', net: 'pending backtest', trades: 0, note: 'auto-tuned nightly; paper only until it clears the cost floor' },
    paperDefault: true,
    builder: true,
    params: [
      { key: 'ema_fast', label: 'EMA fast', group: 'signal', type: 'int', default: 9, min: 3, max: 30 },
      { key: 'ema_mid', label: 'EMA mid', group: 'signal', type: 'int', default: 13, min: 5, max: 50 },
      { key: 'ema_slow', label: 'EMA slow', group: 'signal', type: 'int', default: 21, min: 10, max: 100 },
      { key: 'slope_lookback', label: 'Slope lookback (bars)', group: 'signal', type: 'int', default: 3, min: 1, max: 10 },
      { key: 'atr_len', label: 'ATR length', group: 'market', type: 'int', default: 14, min: 5, max: 40 },
      { key: 'big_candle_atr', label: 'Skip trigger candle > ×ATR', group: 'signal', type: 'float', default: 2.0, min: 1, max: 5, step: 0.25 },
      { key: 'stretch_atr', label: 'Take-profit stretch × ATR', group: 'signal', type: 'float', default: 3.0, min: 1.5, max: 6, step: 0.5 },
      ...TRAIL,
    ],
  },
  {
    id: 'index_options',
    name: 'Index Options',
    kind: 'index',
    statusKey: 'index_options',
    engine: 'CPR + EMA + Supertrend + OI · premium-trail exits',
    instrument: 'NIFTY · BANKNIFTY · SENSEX',
    timeframe: 'buy fast · sell 5m setup + 15m trend',
    blurb:
      'The live index-options engine — naked single-leg buying (60% confidence, OI + liquidity gated) and directional credit selling (5m setup, 15m trend/S&R, premium-trail owns the exit). Chop brakes and the per-index viability cost floor gate the sell lane; BANKNIFTY 4-leg structures stay blocked. Runs through the executor → Trade History.',
    reads: 'Tuned through Settings → Strategy tuning (.env), not the Builder. P&L is in Trade History / Reports (source: index).',
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
