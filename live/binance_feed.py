"""
live/binance_feed.py

Minimal live 15m candle feed for BTC-USD, sourced from Binance's public
klines REST endpoint (BTCUSDT). Same source used for the historical
fixtures the strategy was validated against (fixtures/btc_15m_full.csv),
per prior session notes -- BTC trades 24/7 with no session gaps.

Only ever returns candles whose close_time has already passed. The
currently-forming (unclosed) candle is always dropped -- this is the same
closed-candle-only discipline the strategy/adapter/broker already enforce
end to end.

No API key required (public market data endpoint). No order placement
capability exists in this file at all.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List

import requests

from strategies.btc_trend_v1 import Candle

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
SYMBOL = "BTCUSDT"
INTERVAL = "15m"


def _to_iso(open_time_ms: int) -> str:
    return datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_recent_closed_candles(limit: int = 250) -> List[Candle]:
    """
    Returns up to `limit` most recent FULLY CLOSED 15m BTCUSDT candles,
    oldest first. The currently-forming candle (close_time in the future)
    is filtered out even if Binance includes it in the response.
    """
    resp = requests.get(
        BINANCE_KLINES_URL,
        params={"symbol": SYMBOL, "interval": INTERVAL, "limit": limit + 1},
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json()

    now_ms = int(time.time() * 1000)
    candles: List[Candle] = []
    for row in raw:
        open_time_ms, o, h, l, c, v, close_time_ms = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
        if close_time_ms > now_ms:
            continue  # still forming -- never included
        candles.append(Candle(
            timestamp=_to_iso(open_time_ms),
            open=float(o), high=float(h), low=float(l), close=float(c), volume=float(v),
        ))
    return candles[-limit:]
