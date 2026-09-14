import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { cn } from '../lib/cn'
import { Button } from './ui/Button'

type Flag = {
  flag: string
  enabled: boolean
  description: string
  set_in_env: boolean
}

/**
 * Feature toggles that write .env through the app.
 *
 * Hand-editing .env is a non-starter for anyone but the author, so every
 * non-financial switch lives here. Flags that can move money (trading mode,
 * live arming, broker credentials) are deliberately absent — those belong to
 * the Execution panel and its confirmation step.
 */
export function FeaturesPanel() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['features'],
    queryFn: () => api<{ flags: Flag[] }>('/api/settings/features'),
  })

  const toggle = useMutation({
    mutationFn: (v: { flag: string; enabled: boolean }) =>
      api('/api/settings/features', { method: 'POST', body: JSON.stringify(v) }),
    onSuccess: (r: unknown) => {
      const res = r as { flag?: string; enabled?: boolean; restart_required?: boolean }
      toast[res?.restart_required ? 'warning' : 'success'](
        res?.restart_required
          ? `${res.flag} ${res.enabled ? 'on' : 'off'} — restart the server to apply`
          : `${res?.flag} ${res?.enabled ? 'enabled' : 'disabled'}`,
      )
      void qc.invalidateQueries({ queryKey: ['features'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const flags = data?.flags ?? []

  return (
    <div className="space-y-2">
      <p className="text-xs text-slate-500">
        Written to .env by the app — no manual editing. Anything that can place an order lives in
        the Execution panel instead.
      </p>
      {flags.map((f) => (
        <div
          key={f.flag}
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-800 p-3"
        >
          <div className="min-w-0">
            <p className="font-mono text-xs text-slate-300">{f.flag}</p>
            <p className="text-xs text-slate-500">{f.description}</p>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={cn(
                'rounded-full px-2 py-0.5 text-[10px] font-semibold',
                f.enabled ? 'bg-[var(--up)]/15 text-[var(--up)]' : 'bg-slate-700/50 text-slate-400',
              )}
            >
              {f.enabled ? 'on' : 'off'}
            </span>
            <Button
              variant={f.enabled ? 'secondary' : 'primary'}
              pending={toggle.isPending && toggle.variables?.flag === f.flag}
              onClick={() => toggle.mutate({ flag: f.flag, enabled: !f.enabled })}
            >
              {f.enabled ? 'Turn off' : 'Turn on'}
            </Button>
          </div>
        </div>
      ))}
    </div>
  )
}
