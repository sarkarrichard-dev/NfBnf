import { useQuery } from '@tanstack/react-query'
import { cn } from '../../lib/cn'
import { fx } from '../../lib/theme'
import { api } from '../../lib/api'
import { CollapsibleSection } from '../CollapsibleSection'
import { DhanAuthPanel } from '../DhanAuthPanel'
import { CryptoSetupPanel } from '../CryptoSetupPanel'

type Status = {
  trading_gates?: { dhan_order_ip_whitelist?: { public_ip?: string | null } }
}
type CryptoStatus = {
  egress?: { ipv4?: string | null; delta_sees_ip?: string | null; whitelist_ok?: boolean }
}

function IpStrip({ label, ip, note }: { label: string; ip?: string | null; note: string }) {
  return (
    <div className={cn(fx.card, 'space-y-1')}>
      <p className={fx.cardLabel}>{label}</p>
      <p className="font-mono text-sm tabular-nums text-slate-100">{ip || '—'}</p>
      <p className="text-[10.5px] text-slate-600">{note}</p>
    </div>
  )
}

/** Exchanges — broker connections in one place. Dhan for the index lanes,
 *  Delta Exchange India for crypto. Both write .env through their own
 *  confirmed money-path endpoints, never the feature-toggle panel. */
export function ExchangesView() {
  const status = useQuery({ queryKey: ['status'], queryFn: () => api<Status>('/api/status') })
  const crypto = useQuery({ queryKey: ['crypto', 'status'], queryFn: () => api<CryptoStatus>('/api/crypto/status') })

  const dhanIp = status.data?.trading_gates?.dhan_order_ip_whitelist?.public_ip
  const deltaIp = crypto.data?.egress?.ipv4
  const deltaSees = crypto.data?.egress?.delta_sees_ip

  return (
    <div className="space-y-5">
      <section className={cn(fx.panel, 'p-4 space-y-3')}>
        <h3 className="text-sm font-bold text-slate-100">This computer's IP address — add it to the broker</h3>
        <p className="text-xs text-slate-500">
          SEBI requires a fixed IP address for live orders: both brokers reject them from any address
          not on their allow-list. Add the IP below in the broker's site. This machine uses one IP for both.
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          <IpStrip label="Dhan (order API)" ip={dhanIp} note="Dhan portal → API → Order IP allow-list" />
          <IpStrip
            label="Delta Exchange (IPv4)"
            ip={deltaIp}
            note={
              deltaSees && !crypto.data?.egress?.whitelist_ok
                ? `Delta last saw ${deltaSees} — add that address`
                : 'Delta portal → API keys → IP allow-list'
            }
          />
        </div>
      </section>

      <CollapsibleSection title="Dhan — index F&O" defaultOpen>
        <p className="mb-3 text-[11px] text-slate-500">
          Required: a trading-enabled API access token with order + portfolio scope. No withdraw scope.
          The token expires daily — re-login here each morning.
        </p>
        <DhanAuthPanel />
      </CollapsibleSection>

      <CollapsibleSection title="Delta Exchange India — crypto perps" defaultOpen>
        <p className="mb-3 text-[11px] text-slate-500">
          Required scope: <span className="font-mono text-slate-300">Trading</span>. Do not enable
          <span className="font-mono text-slate-300"> Withdrawal</span> — the lane never needs it and a
          withdraw-capable key is a standing risk.
        </p>
        <CryptoSetupPanel />
      </CollapsibleSection>
    </div>
  )
}
