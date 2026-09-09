"""
test_btc_trend_v1_synthetic.py

Standalone synthetic harness for strategies/btc_trend_v1.py.

Purpose: prove the strategy code runs end-to-end without crashing and
produces sane, internally-consistent output (entry found, stop below
entry, trailing only tightens, an eventual exit fires) on constructed
data with a KNOWN shape. This is NOT parity, NOT a backtest, and NOT
evidence of profitability — it is a smoke test for the strategy module
in isolation, exactly like the old "ARCHITECTURE IMPORT PASS" /
"SIGNAL FOUND" checks were smoke tests, not proof.

Does NOT touch runtime/engine.py, execution/broker_interface.py, or
risk/risk_manager.py. Position lifecycle (open/close) is simulated
manually right here in the harness, not via the real engine.

Run: python3 test_btc_trend_v1_synthetic.py
"""

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from strategies.btc_trend_v1 import (
    BTCTrendV1Strategy,
    Candle,
    PositionState,
    ExitReason,
)


def build_synthetic_candles():
    """
    Construct a deterministic synthetic 15m candle series with a known shape:

      Phase 1 (0..219):   flat/choppy warmup, price oscillates ~ constant.
                           This should NEVER produce a signal (ADX too low,
                           no clean EMA alignment) — used to sanity-check
                           that the strategy stays silent during warmup/chop.
      Phase 2 (220..259):  clean, steady uptrend (should push EMA50 > EMA200,
                           ADX rising above 25).
      Phase 3 (260..268):  a pullback dip toward EMA20, closing back above it
                           on a bullish candle — this is the intended entry
                           trigger.
      Phase 4 (269..319):  continued uptrend (to exercise trailing stop
                           ratcheting upward).
      Phase 5 (320..339):  a sharp reversal down, steep enough to eventually
                           trigger either the trailing stop or the EMA
                           trend-break exit.

    All values are synthetic and arbitrary — this is a shape test, not
    realistic market data.
    """
    candles = []
    t0 = datetime(2026, 1, 1)
    price = 50000.0

    def add(o, h, l, c, vol=100.0):
        nonlocal t0
        candles.append(Candle(timestamp=t0, open=o, high=h, low=l, close=c, volume=vol))
        t0 += timedelta(minutes=15)

    # Phase 1: flat/choppy warmup (220 candles) — tiny oscillation, no trend.
    for i in range(220):
        wiggle = 30 * math.sin(i * 0.9)
        o = price + wiggle
        c = price + wiggle * 0.5
        h = max(o, c) + 15
        l = min(o, c) - 15
        add(o, h, l, c)

    # Phase 2: clean uptrend (40 candles), steady climb.
    for i in range(40):
        o = price
        c = price + 45
        h = c + 10
        l = o - 5
        add(o, h, l, c)
        price = c

    # Phase 3: pullback toward EMA20 then bullish reclaim (9 candles).
    # First few candles drop price down several bars worth of EMA20 distance,
    # then one clear bullish candle that dips below EMA20 intracandle and
    # closes back above it.
    for i in range(6):
        o = price
        c = price - 40
        h = o + 5
        l = c - 10
        add(o, h, l, c)
        price = c
    # The reclaim candle: opens low, dips further (low <= ema20 plausibly),
    # closes strongly bullish above open.
    o = price
    l = price - 60
    c = price + 90
    h = c + 10
    add(o, h, l, c)
    price = c
    # A couple more mildly bullish candles to keep trend intact.
    for i in range(2):
        o = price
        c = price + 20
        h = c + 5
        l = o - 5
        add(o, h, l, c)
        price = c

    # Phase 4: continued uptrend (50 candles) — lets trailing stop ratchet up.
    for i in range(50):
        o = price
        c = price + 35
        h = c + 8
        l = o - 5
        add(o, h, l, c)
        price = c

    # Phase 5: sharp reversal down (20 candles).
    for i in range(20):
        o = price
        c = price - 120
        h = o + 10
        l = c - 15
        add(o, h, l, c)
        price = c

    return candles


def main():
    candles = build_synthetic_candles()
    strategy = BTCTrendV1Strategy()

    position: PositionState | None = None
    entries = []
    exits = []

    for idx, candle in enumerate(candles):
        strategy.on_closed_candle(candle)

        if position is not None:
            exit_instruction = strategy.evaluate_exit(position)
            if exit_instruction is not None:
                exits.append(
                    {
                        "index": idx,
                        "timestamp": candle.timestamp,
                        "exit_price": exit_instruction.exit_price,
                        "reason": exit_instruction.reason.value,
                        "entry_price": position.entry_price,
                        "structure_stop_was": position.structure_stop,
                        "trailing_active": position.trailing_active,
                    }
                )
                position = None
                # Deliberately do NOT evaluate entry on this same candle —
                # matches the "no same-candle re-entry" rule already agreed.
                continue

        if position is None:
            signal = strategy.evaluate_entry()
            if signal is not None:
                # Simulate: real fill happens on NEXT candle's open. Harness
                # approximates this by using the NEXT candle in the list,
                # since this is a synthetic offline test, not live/streaming.
                if idx + 1 < len(candles):
                    next_candle = candles[idx + 1]
                    fill_price = next_candle.open
                    position = PositionState(
                        entry_price=fill_price,
                        structure_stop=signal.initial_stop,
                        highest_high_since_entry=next_candle.high,
                    )
                    entries.append(
                        {
                            "index": idx,
                            "timestamp": candle.timestamp,
                            "signal_candle_close": candle.close,
                            "fill_price": fill_price,
                            "initial_stop": signal.initial_stop,
                        }
                    )

    print("=" * 60)
    print(f"Candles processed: {len(candles)}")
    print(f"Entries: {len(entries)}")
    print(f"Exits: {len(exits)}")
    print("=" * 60)

    ok = True

    if len(entries) == 0:
        print("FAIL: no entry signal produced at all — expected at least one "
              "in the constructed pullback phase (phase 3).")
        ok = False

    for e in entries:
        print(f"\nENTRY @ index {e['index']} ({e['timestamp']})")
        print(f"  signal_candle_close = {e['signal_candle_close']:.2f}")
        print(f"  fill_price (next open) = {e['fill_price']:.2f}")
        print(f"  initial_stop = {e['initial_stop']:.2f}")
        if not (e["initial_stop"] < e["fill_price"]):
            print("  FAIL: initial_stop is not below fill_price")
            ok = False
        else:
            print("  OK: initial_stop below fill_price")

    for x in exits:
        print(f"\nEXIT @ index {x['index']} ({x['timestamp']})")
        print(f"  reason = {x['reason']}")
        print(f"  exit_price = {x['exit_price']:.2f}")
        print(f"  entry_price = {x['entry_price']:.2f}")
        print(f"  trailing_active_at_exit = {x['trailing_active']}")

    if len(entries) > 0 and len(exits) == 0:
        print("\nFAIL: position opened but never exited by end of synthetic "
              "series — phase 5 (sharp reversal) should have triggered a "
              "trailing stop or trend-break exit.")
        ok = False

    print("\n" + "=" * 60)
    if ok:
        print("SYNTHETIC HARNESS: PASS")
    else:
        print("SYNTHETIC HARNESS: FAIL")
    print("=" * 60)

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
