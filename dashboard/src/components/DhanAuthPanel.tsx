import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '../lib/api'
import { Button } from './ui/Button'

export function DhanAuthPanel() {
  const [token, setToken] = useState('')
  const [output, setOutput] = useState('Dhan credentials from .env')

  const saveToken = useMutation({
    mutationFn: () =>
      api('/api/auth/consume-consent', {
        method: 'POST',
        body: JSON.stringify({ token_id: token.trim() }),
      }),
    onSuccess: (r: unknown) => {
      setOutput(JSON.stringify(r, null, 2))
      toast.success('Token saved')
      setToken('')
    },
    onError: (e: Error) => {
      setOutput(e.message)
      toast.error(e.message)
    },
  })

  const totpLogin = useMutation({
    mutationFn: () => api('/api/auth/totp-login', { method: 'POST' }),
    onSuccess: (r: unknown) => {
      setOutput(JSON.stringify(r, null, 2))
      toast.success('TOTP login OK')
    },
    onError: (e: Error) => setOutput(e.message),
  })

  const renew = useMutation({
    mutationFn: () => api('/api/auth/renew-token', { method: 'POST' }),
    onSuccess: (r: unknown) => setOutput(JSON.stringify(r, null, 2)),
    onError: (e: Error) => setOutput(e.message),
  })

  const verify = useMutation({
    mutationFn: () => api('/api/auth/health', { method: 'POST', body: '{}' }),
    onSuccess: (r: unknown) => setOutput(JSON.stringify(r, null, 2)),
    onError: (e: Error) => setOutput(e.message),
  })

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        Paste <code className="text-slate-400">eyJ…</code> JWT from Dhan Web → Generate Access Token,
        or use TOTP auto-login when <code className="text-slate-400">DHAN_PIN</code> +{' '}
        <code className="text-slate-400">DHAN_TOTP_SECRET</code> are in .env.
      </p>
      <div className="flex flex-wrap gap-2">
        <input
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Paste eyJ… JWT or tokenId"
          className="min-w-[16rem] flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
        />
        <Button size="md" variant="primary"
          onClick={() => saveToken.mutate()}
          disabled={!token.trim() || saveToken.isPending}
        >
          Save token
        </Button>
        <Button size="md"
          onClick={() => verify.mutate()}
        >
          Verify
        </Button>
        <Button size="md"
          onClick={() => renew.mutate()}
        >
          Renew
        </Button>
        <Button size="md"
          onClick={() => totpLogin.mutate()}
        >
          TOTP login
        </Button>
      </div>
      <pre className="max-h-48 overflow-auto rounded-md border border-slate-800 bg-slate-950 p-3 text-[11px] text-slate-400">
        {output}
      </pre>
    </div>
  )
}
