# reference/

Third-party projects vendored as **git submodules** for study and code reuse.
Nothing here is imported by `index_ai` at runtime — treat it as a read-only
library of patterns to adapt.

## openalgo (`reference/openalgo`)

- Source: https://github.com/marketcalls/openalgo
- Self-hosted algo-trading platform: unified REST API over 36 Indian brokers,
  Flask 3 + SQLAlchemy backend, React 19 frontend, ZeroMQ WebSocket data.

### What to mine from it

| Need | Where in openalgo |
| --- | --- |
| Dhan order / quote / option-chain normalization | `broker/dhan/` |
| Cross-broker symbol + expiry + strike handling | `broker/`, `services/`, `database/symbol.py` |
| Order place / modify / cancel, order splitting | `services/` |
| Option Greeks, GEX, payoff / strategy builder | `restx_api/`, `blueprints/` (`/tools`) |
| Live market data streaming pattern | `websocket_proxy/` (ZeroMQ, port 8765) |
| Sandbox / paper-trading engine | `sandbox/` |
| REST API surface design | `restx_api/` (`/api/v1/`, ~57 endpoints) |

### Working with the submodule

```bash
# First checkout after cloning this repo
git submodule update --init reference/openalgo

# Pull upstream changes later
git submodule update --remote reference/openalgo
git add reference/openalgo && git commit -m "Bump openalgo reference"
```

Pinned commit is whatever `git -C reference/openalgo rev-parse HEAD` reports;
it only moves when you deliberately bump it.
