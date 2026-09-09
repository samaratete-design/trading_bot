"""
fixtures/freeze_btc_snapshot.py

Fetches BTC-USD 15m OHLCV data ONCE from Binance's public REST API and
freezes it as a fixed CSV file. Uses only `requests` + stdlib `csv` —
no pandas/numpy/yfinance dependency, to avoid Termux packaging issues.

This file must NEVER be re-generated/overwritten for the same backtest run
— that would defeat the purpose of a frozen snapshot. If you need fresh
data later, save it under a NEW filename with a date suffix, and treat it
as a separate, independent test.

Data source note: Binance klines, not Yahoo. This is a DELIBERATE choice
for BTC specifically (24/7 real exchange data, no session gaps) — it does
NOT set a precedent for EURUSD/XAUUSD, which are separate, independent
strategies and may use a different source entirely.

Run once: python3 fixtures/freeze_btc_snapshot.py
"""

import csv
import time
from datetime import datetime, timezone

import requests

SYMBOL = "BTCUSDT"       # Binance spot symbol (USDT-margined, closest public proxy to BTC-USD)
INTERVAL = "15m"
LIMIT = 1000              # max klines per request allowed by Binance API
DAYS = 60                 # matches the "60d" period used by the old Yahoo baseline
BASE_URL = "https://api.binance.com/api/v3/klines"


def fetch_all_klines():
    end_time = int(time.time() * 1000)
    start_time = end_time - DAYS * 24 * 60 * 60 * 1000

    all_rows = []
    cursor = start_time

    while True:
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": cursor,
            "limit": LIMIT,
        }
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        batch = resp.json()

        if not batch:
            break

        all_rows.extend(batch)

        last_open_time = batch[-1][0]
        next_cursor = last_open_time + 1

        if next_cursor <= cursor:
            break  # safety: avoid infinite loop if API returns something odd
        cursor = next_cursor

        if last_open_time >= end_time:
            break

        time.sleep(0.3)  # be polite to the public API

    return all_rows


def main():
    print(f"Fetching {SYMBOL} {INTERVAL} klines from Binance, last {DAYS}d ...")
    raw = fetch_all_klines()

    if not raw:
        print("FAIL: no data returned. Check network/API availability.")
        return 1

    # Binance kline fields (index):
    # 0 open_time, 1 open, 2 high, 3 low, 4 close, 5 volume, 6 close_time, ...
    rows = []
    seen_open_times = set()
    for k in raw:
        open_time_ms = k[0]
        if open_time_ms in seen_open_times:
            continue  # de-dupe overlapping pagination
        seen_open_times.add(open_time_ms)
        ts = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
        rows.append({
            "timestamp": ts.isoformat(),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })

    rows.sort(key=lambda r: r["timestamp"])

    out_path = "fixtures/btc_15m_snapshot.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)

    fetched_at = datetime.now(timezone.utc).isoformat()
    with open("fixtures/btc_15m_snapshot.meta.txt", "w") as f:
        f.write(f"symbol={SYMBOL} (Binance spot, proxy for BTC-USD)\n")
        f.write(f"interval={INTERVAL}\n")
        f.write(f"days={DAYS}\n")
        f.write(f"source=binance_api\n")
        f.write(f"fetched_at_utc={fetched_at}\n")
        f.write(f"rows={len(rows)}\n")
        f.write(f"first_timestamp={rows[0]['timestamp']}\n")
        f.write(f"last_timestamp={rows[-1]['timestamp']}\n")

    print(f"OK: saved {len(rows)} rows to {out_path}")
    print(f"Range: {rows[0]['timestamp']} -> {rows[-1]['timestamp']}")
    print("Metadata written to fixtures/btc_15m_snapshot.meta.txt")
    print("\nIMPORTANT: this file is now FROZEN. Do not re-run this script")
    print("and overwrite it if you want later tests to remain comparable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
