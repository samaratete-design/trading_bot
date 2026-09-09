"""
Entry Hypothesis Scanner — READ ONLY
====================================
Purpose:
    Scan btc_15m_full.csv (26k+ candles) to test if the "shallow pullback + 
    extreme close location" pattern is a systemic trap across the broader dataset,
    independent of any specific trade strategy rules.

IMPORTANT:
    - READ ONLY (does not modify strategies or run live trades)
    - stdlib only (csv, math)
"""

import csv
import math
import os

INPUT_FILE = "fixtures/btc_15m_full.csv"

def f(val):
    try:
        x = float(val)
        if math.isfinite(x):
            return x
    except (TypeError, ValueError):
        pass
    return None

def main():
    path = INPUT_FILE
    if not os.path.exists(path):
        print(f"ERROR: File not found: {path}")
        return 1

    print("=" * 75)
    print("ENTRY HYPOTHESIS SCANNER — BROAD DATASET CHECK")
    print("=" * 75)

    candles = []
    with open(path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            o = f(row.get("open"))
            h = f(row.get("high"))
            l = f(row.get("low"))
            c = f(row.get("close"))
            t = row.get("timestamp")
            if None not in (o, h, l, c):
                candles.append({"time": t, "open": o, "high": h, "low": l, "close": c})

    print(f"Loaded valid candles: {len(candles)}")

    if len(candles) < 50:
        print("Not enough candles to scan.")
        return 1

    # حساب مؤشرات بسيطة مبدئية (مثل ATR مبسط و Close Location)
    # Close Location Pct = (Close - Low) / (High - Low)
    extreme_close_shallow_pullback_count = 0
    total_checked = 0

    # سناخذ نافذة لفحص سلوك الشمعات الصاعدة المتطرفة
    for i in range(20, len(candles) - 5):
        c_curr = candles[i]
        h = c_curr["high"]
        l = c_curr["low"]
        c = c_curr["close"]
        o = c_curr["open"]

        rng = h - l
        if rng == 0:
            continue

        close_loc = (c - l) / rng
        
        # لنفترض أننا نبحث عن الشموع الصاعدة القوية جداً (Close Location >= 0.85)
        if close_loc >= 0.85 and c > o:
            total_checked += 1
            # هل تلتها شمعة انعكاسية مباشرة في الـ 3 شمعات التالية؟ (شبه الـ Early Failure)
            next_lows = [candles[i+j]["low"] for j in range(1, 4)]
            # لو أدنى سعر تالٍ كسر قاع الشمعة الحالية أو اقترب بشدة
            if min(next_lows) < l:
                extreme_close_shallow_pullback_count += 1

    print(f"Total strong bullish momentum candles found: {total_checked}")
    if total_checked > 0:
        ratio = (extreme_close_shallow_pullback_count / total_checked) * 100
        print(f"Candles followed by immediate breakdown (Next 3 candles broke Low): {extreme_close_shallow_pullback_count} ({ratio:.2f}%)")
    
    print("=" * 75)
    print("Diagnostic complete. No strategy files were modified.")
    print("=" * 75)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
