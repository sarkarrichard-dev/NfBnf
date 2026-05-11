# Trading AI Workstation

A local Indian-market learning and paper-trading dashboard.

## Open It

Double-click:

```text
Launch Trading AI Dashboard.cmd
```

Then use the browser screen at:

```text
http://127.0.0.1:8000
```

## Folder Map

- `dashboard` - the fresh web screen.
- `trading_ai_engine` - the app brain, server, learning code, risk checks, and paper trading.
- `Your Trading Data` - private downloaded market data, AI model files, and local memory.
- `Files To Teach AI` - put your own CSV or Excel files here.
- `guides` - plain-language safety and roadmap notes.
- `learning_examples` - optional examples for Hugging Face dataset work.

## Useful Commands

```powershell
python -m trading_ai_engine.server.main
python -m trading_ai_engine.ml.market_learn_cli --years 7 --max-symbols 25 --download --train
python -m trading_ai_engine.ml.hf_cli --market-training --preview-rows 5
python -m ruff check .
```

Live broker orders stay blocked unless the safety gates are deliberately changed.
