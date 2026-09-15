import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
// Self-hosted — bundled with the app so rendering never depends on a live
// fonts.googleapis.com request (blocked on some networks, silently falling
// back to a plain system font).
import '@fontsource/plus-jakarta-sans/400.css'
import '@fontsource/plus-jakarta-sans/500.css'
import '@fontsource/plus-jakarta-sans/600.css'
import '@fontsource/plus-jakarta-sans/700.css'
import '@fontsource/plus-jakarta-sans/800.css'
import '@fontsource/geist-mono/400.css'
import '@fontsource/geist-mono/500.css'
import '@fontsource/geist-mono/600.css'
import '@fontsource/orbitron/700.css'
import '@fontsource/orbitron/800.css'
import './index.css'
import { queryClient } from './lib/queryClient'

const App = lazy(() => import('./App.tsx'))

function LoadingShell() {
  return (
    <main className="mx-auto flex min-h-screen max-w-lg items-center justify-center px-4">
      <p className="text-sm text-slate-400">Loading dashboard…</p>
    </main>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <Suspense fallback={<LoadingShell />}>
        <App />
      </Suspense>
      <Toaster richColors position="top-right" />
    </QueryClientProvider>
  </StrictMode>,
)
