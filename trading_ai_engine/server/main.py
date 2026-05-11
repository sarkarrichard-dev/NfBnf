from __future__ import annotations

import os

from dotenv import load_dotenv


def run() -> None:
    import uvicorn

    load_dotenv()
    port = int(os.environ.get("TRADING_AI_PORT", os.environ.get("PORT", "8000")))
    uvicorn.run(
        "trading_ai_engine.server.app:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        factory=False,
        use_colors=False,
    )


if __name__ == "__main__":
    run()
