import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { api } from '../lib/api'
import { money, pnlClass } from '../lib/pnl'
import { cn } from '../lib/cn'

type DhanPosition = {
  leg_label?: string
  trading_symbol?: string
  exchange_segment?: string
  net_qty?: number
  realized_profit?: number
  unrealized_profit?: number
}

type DhanTrade = {
  exchange_time_ist?: string
  create_time_ist?: string
  leg_label?: string
  trading_symbol?: string
  traded_quantity?: number
  traded_price?: number
  product_type?: string
  order_id?: string
}

type DhanAccount = {
  funds?: Record<string, number>
  positions?: DhanPosition[]
  tradebook?: DhanTrade[]
  tradebook_count?: number
  orders_today_count?: number
  positions_note?: string
  journal_broker_mismatch?: string
  error?: string
  errors?: string[]
  updated_at_ist?: string
}

export function DhanAccountPanel() {
  const { data, refetch, isFetching, error } = useQuery({
    queryKey: ['dhan-account'],
    queryFn: () => api<DhanAccount>('/api/dhan/account'),
    refetchInterval: 45_000,
    retry: 1,
  })

  const errMsg =
    data?.error ||
    data?.errors?.join(' · ') ||
    data?.journal_broker_mismatch ||
    (error instanceof Error ? error.message : null)

  const funds = data?.funds || {}

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-slate-500">
          Fund limits and trade book from Dhan
          {data?.updated_at_ist ? ` · ${data.updated_at_ist}` : ''}
        </p>
        <button
          type="button"
          onClick={() => void refetch()}
          className="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-xs text-slate-300"
        >
          <RefreshCw size={12} className={isFetching ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {errMsg ? (
        <p className="rounded border border-amber-500/30 bg-amber-950/20 px-3 py-2 text-xs text-amber-200">
          {errMsg}
        </p>
      ) : null}

      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {[
          ['Available', funds.available_balance],
          ['Utilized', funds.utilized_amount],
          ['SOD limit', funds.sod_limit],
          ['Withdrawable', funds.withdrawable_balance],
          ['Collateral', funds.collateral_amount],
          ['Receivable', funds.receivable_amount],
        ].map(([label, val]) => (
          <article key={String(label)} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
            <span className="text-xs text-slate-500">{label}</span>
            <p className="text-lg font-semibold tabular-nums">{money(val as number | undefined)}</p>
          </article>
        ))}
      </div>

      <MiniTable
        title="Open positions (Dhan)"
        headers={['Symbol', 'Segment', 'Qty', 'Realized', 'Unrealized']}
        rows={(data?.positions || []).map((p) => [
          p.leg_label || p.trading_symbol || '—',
          p.exchange_segment || '',
          String(p.net_qty ?? '—'),
          money(p.realized_profit),
          money(p.unrealized_profit),
        ])}
        empty="No open positions on Dhan."
      />

      <MiniTable
        title="Trade book today"
        headers={['Time', 'Leg', 'Qty', 'Price', 'Product', 'Order ID']}
        rows={(data?.tradebook || []).map((t) => [
          t.exchange_time_ist || t.create_time_ist || '—',
          t.leg_label || t.trading_symbol || '—',
          String(t.traded_quantity ?? '—'),
          t.traded_price != null ? money(t.traded_price) : '—',
          t.product_type || '',
          t.order_id || '',
        ])}
        empty="No executed trades today on Dhan."
        footer={
          data?.tradebook_count != null
            ? `${data.tradebook_count} fill(s) today${data.positions_note ? ` · ${data.positions_note}` : ''}`
            : undefined
        }
      />
    </div>
  )
}

function MiniTable({
  title,
  headers,
  rows,
  empty,
  footer,
}: {
  title: string
  headers: string[]
  rows: string[][]
  empty: string
  footer?: string
}) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-medium text-slate-400">{title}</h3>
      <div className="max-h-48 overflow-auto rounded-lg border border-slate-800">
        <table className="min-w-full text-xs">
          <thead className="sticky top-0 bg-slate-950 text-slate-500">
            <tr>
              {headers.map((h) => (
                <th key={h} className="px-2 py-1.5 text-left font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {!rows.length ? (
              <tr>
                <td colSpan={headers.length} className="px-2 py-4 text-center text-slate-500">
                  {empty}
                </td>
              </tr>
            ) : (
              rows.map((row, i) => (
                <tr key={i} className="border-t border-slate-800/60 text-slate-300">
                  {row.map((cell, j) => (
                    <td
                      key={j}
                      className={cn(
                        'px-2 py-1.5 tabular-nums',
                        j >= 3 && cell.startsWith('+') && pnlClass(1),
                        j >= 3 && cell.startsWith('-') && cell !== '—' && pnlClass(-1),
                      )}
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {footer ? <p className="mt-1 text-[11px] text-slate-500">{footer}</p> : null}
    </div>
  )
}
