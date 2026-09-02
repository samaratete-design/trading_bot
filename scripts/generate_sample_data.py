"""
scripts/generate_sample_data.py

Generates data/historical_sample.csv with enough candles (and a clear
trend reversal) to exercise the MA crossover strategy's warm-up period
and produce at least one real crossover signal.

Run directly:
    python scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta

OUTPUT_PATH = "data/historical_sample.csv"


def generate_rows() -> list[tuple[datetime, float]]:
    rows: list[tuple[datetime, float]] = []
    start = datetime(2026, 1, 1)
    price = 50_000.0

    # Phase 1: mild downtrend/chop for 20 candles — builds the slow MA
    # on a bearish/flat base so the strategy starts in a "BEAR" trend.
    for i in range(20):
        price -= 30 + (i % 3) * 5
        rows.append((start + timedelta(hours=i), price))

    # Phase 2: sharp rally for 10 candles — flips fast MA above slow MA
    # (bullish crossover -> BUY signal), then keeps climbing so the
    # earlier entry's take_profit gets hit.
    for i in range(20, 30):
        price += 250
        rows.append((start + timedelta(hours=i), price))

    # Phase 3: sharp reversal down for 10 more candles — flips the
    # trend back to "BEAR" (bearish crossover -> SELL signal), and
    # keeps dropping so that signal's take_profit also gets hit.
    for i in range(30, 40):
        price -= 300
        rows.append((start + timedelta(hours=i), price))

    return rows


def write_csv(rows: list[tuple[datetime, float]], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])

        prev_close = rows[0][1]
        for ts, close in rows:
            open_ = prev_close
            high = max(open_, close) + 20
            low = min(open_, close) - 20
            writer.writerow(
                [ts.isoformat(), round(open_, 2), round(high, 2), round(low, 2), round(close, 2), 1.5]
            )
            prev_close = close


def main() -> int:
    rows = generate_rows()
    write_csv(rows, OUTPUT_PATH)
    print(f"Wrote {len(rows)} candles to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
