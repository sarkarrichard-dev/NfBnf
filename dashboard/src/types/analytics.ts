export type PeriodStats = {
  trades?: number
  closed?: number
  open?: number
  wins?: number
  losses?: number
  win_rate?: number | null
  pnl_rupees?: number
  by_instrument?: Record<string, number>
  by_action?: Record<string, number>
  by_leg?: Record<string, number>
}

export type LogRow = {
  trade_id?: string
  leg_index?: number
  leg_count?: number
  open_time_ist?: string
  close_time_ist?: string | null
  instrument?: string
  side?: string
  strike?: string | number | null
  option_type?: string
  quantity?: number
  avg_entry?: number | null
  avg_exit?: number | null
  mark_price?: number | null
  /** Price quote currency for this leg — 'INR' (index, default) or 'USD' (crypto perps). */
  quote_ccy?: 'INR' | 'USD'
  leg_mtm?: number | null
  leg_pnl?: number | null
  spread_pnl?: number | null
  display_pnl?: number | null
  /** ₹ committed to the whole trade — premium paid (buy) or combined margin at
   *  risk (sell + hedge). Trade-level: set on leg 0 only. */
  capital_deployed?: number | null
  capital_kind?: 'premium' | 'margin' | string
  is_open?: boolean
  status?: string
  display_status?: string
  mode?: string
  entry_session_ok?: boolean
  is_live?: boolean
  expiry?: string
  broker_order_id?: string
  broker_status_line?: string | null
  mtm_updated_at_ist?: string | null
  row_class?: string
  leg_group_class?: string
}

export type TradeRow = {
  id: string
  created_at?: string
  created_at_ist?: string
  closed_at_ist?: string | null
  instrument?: string
  action?: string
  lot_label?: string
  pnl?: number | null
  mtm_pnl?: number | null
  mtm_updated_at_ist?: string | null
  current_option_ltp?: number | null
  is_open?: boolean
  entry_session_ok?: boolean
  is_live?: boolean
  status?: string
  display_status?: string
  capital_deployed?: number | null
  capital_kind?: 'premium' | 'margin' | string
  legs_detail?: Array<Record<string, unknown>>
  mtm_error?: string | null
}

export type AnalyticsResponse = {
  generated_at_ist?: string
  open_mtm_rupees?: number
  today_open_mtm_rupees?: number
  open_positions?: number
  stale_open_positions?: number
  overview?: PeriodStats
  today?: PeriodStats
  week?: PeriodStats
  month?: PeriodStats
  daily_series?: Array<{ period: string; pnl_rupees: number }>
  trades?: TradeRow[]
  log_rows?: LogRow[]
  live_summary?: {
    open?: number
    realized_pnl?: number
    open_mtm?: number
  }
  paper_summary?: {
    open?: number
    realized_pnl?: number
    open_mtm?: number
  }
}

export type JournalResponse = {
  rows?: TradeRow[]
  log_rows?: LogRow[]
  updated_at_ist?: string
}

export type LiveMtmResponse = {
  trades?: TradeRow[]
  log_rows?: LogRow[]
  open_mtm_rupees?: number
  updated_at_ist?: string
  error?: string
}

export type PeriodKey = 'today' | 'week' | 'month' | 'all' | 'custom'

/** IST calendar dates (YYYY-MM-DD) for the custom range. Empty = unset. */
export type DateRange = { from: string; to: string }

export type PositionsFilter = 'all' | 'profit' | 'loss'
