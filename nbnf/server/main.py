from __future__ import annotations


def run() -> None:
    import uvicorn

    uvicorn.run(
        "nbnf.server.app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        factory=False,
    )


if __name__ == "__main__":
    run()
