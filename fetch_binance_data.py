"""
fetch_binance_data.py

Fetches BTCUSDT 15m klines from Binance's public REST API (no API key
needed for market data) and writes them to a CSV matching the exact
column format already used by fixtures/btc_15m_snapshot.csv:
    timestamp, open, high, low, close, volume

Also runs a strict gap/integrity check on the result: every consecutive
pair of rows must be exactly 15 minutes apart, no duplicates, no missing
candles. Gaps are NEVER interpolated or silently dropped -- they are
reported explicitly, and the script tells you clearly whether the output
is safe to backtest on.

Does NOT touch strategies/btc_trend_v1.py or any existing fixture file.
Uses only the Python standard library (urllib, json, csv) -- no pip
install, no pandas, no third-party dependencies, per the Termux
constraint already established.

Usage:
    python3 fetch_binance_data.py
    python3 fetch_binance_data.py --months 12
    python3 fetch_binance_data.py --months 6 --out fixtures/btc_15m_9mo.csv

Binance kline REST reference (public, unauthenticated):
    GET https://api.binance.com/api/v3/klines
    params: symbol, interval, startTime, endTime, limit(<=1000)
    response row: [open_time_ms, open, high, low, close, volume,
                   close_time_ms, ...]
"""

import argparse
import csv
import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

SYMBOL = "BTCUSDT"
INTERVAL = "15m"
INTERVAL_MINUTES = 15
BASE_URL = "https://api.binance.com/api/v3/klines"
MAX_LIMIT = 1000  # Binance's per-request cap for klines
REQUEST_SLEEP_SECONDS = 0.3  # polite spacing between requests
MAX_RETRIES = 5


def parse_args():
    p = argparse.ArgumentParser(description="Fetch BTCUSDT 15m klines from Binance")
    p.add_argument("--months", type=float, default=9,
                    help="How many months back to fetch (default 9, "
                         "within the agreed 6-12 month range)")
    p.add_argument("--out", type=str, default="fixtures/btc_15m_full.csv",
                    help="Output CSV path")
    p.add_argument("--gap-report", type=str, default="fixtures/btc_15m_full_gap_report.txt",
                    help="Path to write the gap/integrity report")
    return p.parse_args()


def fetch_klines(start_ms, end_ms):
    """Fetch one page (<=1000 candles) of klines in [start_ms, end_ms)."""
    params = (
        f"symbol={SYMBOL}&interval={INTERVAL}"
        f"&startTime={start_ms}&endTime={end_ms}&limit={MAX_LIMIT}"
    )
    url = f"{BASE_URL}?{params}"

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            wait = min(2 ** attempt, 30)
            print(f"  [warn] fetch failed (attempt {attempt}/{MAX_RETRIES}): {e} "
                  f"-- retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch klines after {MAX_RETRIES} attempts: {last_err}")


def fetch_all_klines(start_dt, end_dt):
    """
    Page through Binance klines from start_dt to end_dt (both UTC-aware
    datetimes), returning a flat list of raw kline rows, oldest first,
    deduplicated by open_time.
    """
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    all_rows = []
    seen_open_times = set()
    cursor = start_ms
    page = 0

    while cursor < end_ms:
        page += 1
        batch = fetch_klines(cursor, end_ms)
        if not batch:
            break

        new_count = 0
        for row in batch:
            open_time = row[0]
            if open_time in seen_open_times:
                continue
            seen_open_times.add(open_time)
            all_rows.append(row)
            new_count += 1

        print(f"  page {page}: got {len(batch)} candles ({new_count} new), "
              f"total so far {len(all_rows)}")

        last_open_time = batch[-1][0]
        next_cursor = last_open_time + (INTERVAL_MINUTES * 60 * 1000)
        if next_cursor <= cursor:
            # Safety: prevent an infinite loop if Binance ever returns a
            # non-advancing page.
            break
        cursor = next_cursor

        if len(batch) < MAX_LIMIT:
            # Fewer than a full page means we've reached the end of
            # available data for this range.
            break

        time.sleep(REQUEST_SLEEP_SECONDS)

    return all_rows


def format_timestamp(open_time_ms):
    dt = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S+00:00")


def write_csv(rows, out_path):
    rows_sorted = sorted(rows, key=lambda r: r[0])
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for row in rows_sorted:
            open_time_ms = row[0]
            o, h, l, c, v = row[1], row[2], row[3], row[4], row[5]
            writer.writerow([
                format_timestamp(open_time_ms),
                float(o), float(h), float(l), float(c), float(v),
            ])
    return len(rows_sorted)


def gap_check(rows, gap_report_path):
    """
    Strict integrity check: every consecutive pair of candles must be
    exactly INTERVAL_MINUTES apart. No interpolation, no silent fixes --
    every gap and every duplicate is recorded and reported.
    """
    rows_sorted = sorted(rows, key=lambda r: r[0])
    n = len(rows_sorted)

    gaps = []
    duplicates = []
    expected_delta_ms = INTERVAL_MINUTES * 60 * 1000

    for i in range(1, n):
        prev_t = rows_sorted[i - 1][0]
        curr_t = rows_sorted[i][0]
        delta = curr_t - prev_t

        if delta == 0:
            duplicates.append((prev_t, curr_t))
        elif delta != expected_delta_ms:
            missing_candles = (delta // expected_delta_ms) - 1
            gaps.append({
                "after": format_timestamp(prev_t),
                "before": format_timestamp(curr_t),
                "delta_minutes": delta / 60000,
                "missing_candles": missing_candles,
            })

    with open(gap_report_path, "w") as f:
        f.write(f"Gap/integrity report for {SYMBOL} {INTERVAL}\n")
        f.write(f"Total candles: {n}\n")
        f.write(f"Duplicates found: {len(duplicates)}\n")
        f.write(f"Gaps found: {len(gaps)}\n")
        f.write("=" * 70 + "\n")
        if duplicates:
            f.write("\nDUPLICATE TIMESTAMPS:\n")
            for prev_t, curr_t in duplicates:
                f.write(f"  {format_timestamp(prev_t)} == {format_timestamp(curr_t)}\n")
        if gaps:
            f.write("\nGAPS (missing candles):\n")
            for g in gaps:
                f.write(
                    f"  after {g['after']} -> before {g['before']}  "
                    f"(delta {g['delta_minutes']:.1f} min, "
                    f"~{g['missing_candles']} candle(s) missing)\n"
                )
        if not duplicates and not gaps:
            f.write("\nNo gaps or duplicates found. Series is fully contiguous.\n")

    return gaps, duplicates


def main():
    args = parse_args()

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=args.months * 30.44)  # avg month length

    print(f"Fetching {SYMBOL} {INTERVAL} klines")
    print(f"  from: {start_dt.strftime('%Y-%m-%d %H:%M:%S+00:00')}")
    print(f"  to:   {end_dt.strftime('%Y-%m-%d %H:%M:%S+00:00')}")
    print(f"  ({args.months} months requested -- agreed range is 6-12 months)")
    print()

    raw_rows = fetch_all_klines(start_dt, end_dt)
    print(f"\nFetched {len(raw_rows)} raw candles total.")

    n_written = write_csv(raw_rows, args.out)
    print(f"Wrote {n_written} candles to {args.out}")

    gaps, duplicates = gap_check(raw_rows, args.gap_report)
    print(f"\nGap/integrity check written to {args.gap_report}")
    print(f"  Duplicates: {len(duplicates)}")
    print(f"  Gaps:       {len(gaps)}")

    print()
    print("=" * 70)
    if gaps or duplicates:
        print("RESULT: INTEGRITY ISSUES FOUND.")
        print("Do NOT run the Baseline/Test D backtest on this file until")
        print(f"you've reviewed {args.gap_report} and decided how to handle")
        print("each gap explicitly (e.g. re-fetch that range, or exclude it")
        print("with a documented, non-arbitrary boundary). No interpolation.")
    else:
        print("RESULT: CLEAN. No gaps, no duplicates -- safe to treat as")
        print("a single continuous series for Baseline/Test D/walk-forward.")
    print("=" * 70)


if __name__ == "__main__":
    main()
