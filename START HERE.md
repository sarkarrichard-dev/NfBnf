# Start Here

1. Copy `.env.example` to `.env` if you do not have one yet.
2. Add Dhan credentials and `DHAN_CLIENT_ID` (see below).
3. Add **`HF_TOKEN`** for Hugging Face FinBERT learning (get a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)). Keep it only in `.env` — do not commit it to git.
4. Double-click `Start Index Options AI.cmd`.
5. Choose `Start AI server`.
6. Choose `Open dashboard`.
7. Use the Dhan Login panel: **Check setup** → **Create Login Link** → complete login in the browser → paste **tokenId** (or the full redirect URL) → **Save Token**.
8. On **Learning from outcomes**, click **Refresh** — you should see ML model stats and **Hugging Face: Active** if `HF_TOKEN` is set.

### Dhan login not working?

- **`.env` must have** `DHAN_API_KEY`, `DHAN_API_SECRET`, and **`DHAN_CLIENT_ID`** (numeric client id from Dhan — not the API key).
- **Redirect URL** on web.dhan.co: `http://127.0.0.1:8000/dhan/oauth/callback` — after login the token saves automatically and the dashboard opens. You can still paste `tokenId` manually if needed.
- Do **not** paste `consentAppId` — only `tokenId` from step 2.
- Errors now show in the auth panel (HTTP 400 with Dhan’s message). Use **Check setup** to see missing fields.
- Optional: generate a 24h token on web.dhan.co and set `DHAN_ACCESS_TOKEN` manually (skip the 3-step flow).
9. Use `Stop AI server` before closing the controller when you want port 8000 closed.

The app is paper trading by default. Live trading needs `TRADING_MODE=LIVE` and `ALLOW_LIVE_TRADING=true`.

Use Live Trade Planner first. It will show why a trade is allowed or blocked before any order can be sent.

## If the dashboard shows dashes or “not working”

1. **Stop anything else on port 8000** (old “Trading Workstation” or a stuck Python process).
2. Start **this** app only: `Start Index Options AI.cmd` → **Start AI server**, or `python -m index_ai.server`.
3. Open `http://127.0.0.1:8000` and hard-refresh (Ctrl+F5).
4. In the server window you should **not** see `[Trading Workstation]` — that is the old app. This app logs `Index Options AI` / `index_ai.server`.
5. After start, the browser should call **`GET /api/status`** (check the server log). If you only see `GET /` and static files, the page is open without the API server running.
