# Index Options AI — Dashboard

Single polished React UI served at **`http://127.0.0.1:8000/`** when you run `Start Index Options AI.cmd`.

There is no separate dev URL in normal use — the algo server serves the built app from `dashboard/dist/`.

## Stack

React + TypeScript + Vite + Tailwind v4 + TanStack Query

## Normal use

1. Double-click **`Start Index Options AI.cmd`**
2. Press **Enter** (default: Start) — builds the dashboard if needed, starts the server, opens the browser once

## Optional: UI development only

If you are editing React source and want hot reload:

```powershell
# Terminal 1 — algo server
python -m index_ai.server

# Terminal 2 — Vite dev (proxies /api to :8000)
cd dashboard
npm install
npm run dev
```

Use `http://127.0.0.1:5173` only while developing UI components. For trading, always use **`http://127.0.0.1:8000`**.

## Manual build

```powershell
cd dashboard
npm install
npm run build
```

Output: `dashboard/dist/` (served by FastAPI at `/`).
