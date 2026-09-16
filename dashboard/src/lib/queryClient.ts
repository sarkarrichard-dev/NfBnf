import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
      // A trading dashboard's numbers must stay live even when the tab isn't
      // focused (Richard, 2026-09-16) — React Query pauses refetchInterval in
      // the background by default; this turns that off everywhere.
      refetchIntervalInBackground: true,
    },
  },
})
