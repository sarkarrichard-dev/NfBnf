"""Crypto section — Delta Exchange India, separate from the index (`index_ai/`) code.

Its own broker, its own `.env` keys (`DELTA_*` / `CRYPTO_*`), its own journals in
`memory/crypto_*`. Shares only the Telegram helper and the `.env` writer.

Phase 1 (this commit): Delta REST client, contract master, market data, a cost
skeleton, and `/api/crypto` health + credential endpoints. No strategy, no orders.
"""
