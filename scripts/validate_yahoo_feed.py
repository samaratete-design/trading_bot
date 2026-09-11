"""
scripts/validate_yahoo_feed.py

Manual, real-network sanity check for live/yahoo_feed.py. Not part of the
unit test suite (tests/test_yahoo_feed.py mocks the network deliberately)
-- run this by hand to confirm Yahoo's endpoint still returns the shape
YahooFeed expects, before trusting it in run_live_paper.py.

Usage:
    python3 scripts/validate_yahoo_feed.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from live.yahoo_feed import YahooFeed, YahooFeedError


def main() -> int:
    feed = YahooFeed(symbol="BTC-USD", interval="15m")
    print(f"[validate] fetching 250 closed candles for {feed.symbol} @ {feed.interval} ...")

    try:
        candles = feed.fetch_closed_candles(limit=250)
    except YahooFeedError as e:
        print(f"[FAIL] {e}")
        return 1

    ok = True

    if len(candles) != 250:
        print(f"[FAIL] expected exactly 250 candles, got {len(candles)}")
        ok = False

    now = datetime.now(timezone.utc)
    for c in candles:
        if not isinstance(c.timestamp, datetime):
            print(f"[FAIL] non-datetime timestamp: {c.timestamp!r}")
            ok = False
            break
        if c.timestamp > now:
            print(f"[FAIL] candle timestamp in the future: {c.timestamp}")
            ok = False
            break

    timestamps = [c.timestamp for c in candles]
    if timestamps != sorted(timestamps):
        print("[FAIL] candles not sorted oldest-first")
        ok = False
    if len(set(timestamps)) != len(timestamps):
        print("[FAIL] duplicate timestamps in output")
        ok = False

    last = candles[-1]
    print(f"[validate] newest candle: {last.timestamp}  O={last.open} H={last.high} "
          f"L={last.low} C={last.close} V={last.volume}")

    if ok:
        print("[PASS] 250 valid, sorted, deduped, closed candles returned")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
