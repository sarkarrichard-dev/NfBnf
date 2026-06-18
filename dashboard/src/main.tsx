import { StrictMode, Suspense, lazy } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
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
