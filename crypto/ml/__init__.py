"""Crypto's own learning stack — wholly separate from ``index_ai.brain``.

Same discipline (chronological walk-forward, a gate that only arms if it beats
trading everything out-of-sample), its own model files under
``memory/models/crypto_*``, its own feature vector. Nothing here imports from
``index_ai.brain``. The gate is dormant until there is enough forward data.
"""
