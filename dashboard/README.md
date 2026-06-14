# React dashboard (Index Options AI)

Single UI served at `http://127.0.0.1:8000/` after build.

## Stack

React + TypeScript + Vite + Tailwind v4 + TanStack Query

## Development

```powershell
cd dashboard
npm install
npm run dev
```

Open `http://127.0.0.1:5173` (proxies `/api/*` to port 8000).

## Production build (required before starting the algo server)

```powershell
cd dashboard
npm install
npm run build
```

Output goes to `dashboard/dist/`. FastAPI serves that folder at `/`.

## Features

- Today's positions (Dhan-style P&L table, 1.5s MTM refresh)
- Trade log grouped by index
- Execution (Paper/Live, lots)
- Dhan account, auth, auto scanner, backtest, learning panels
