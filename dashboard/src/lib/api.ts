export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = (await res.json()) as { detail?: unknown; error?: string }
      if (body?.detail != null) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      } else if (body?.error) {
        detail = body.error
      }
    } catch {
      /* noop */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}
