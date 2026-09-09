"""
Double-Condition Scanner — READ ONLY
====================================
Purpose:
    Test the combined hypothesis on btc_15m_full.csv (26k+ candles):
    Does combining "Extreme Close Location" + "Shallow Pullback" 
    drastically increase the immediate breakdown (trap) rate?
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
    print("DOUBLE-CONDITION SCANNER — SHALLOW PULLBACK + EXTREME CLOSE")
    print("=" * 75)

    candles = []
    with open(path, "r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            o, h, l, c = f(row.get("open")), f(row.get("high")), f(row.get("low")), f(row.get("close"))
            if None not in (o, h, l, c):
                candles.append({"open": o, "high": h, "low": l, "close": c})

    print(f"Loaded valid candles: {len(candles)}")
    if len(candles) < 50:
        return 1

    double_trap_total = 0
    double_trap_breakdown = 0

    for i in range(20, len(candles) - 5):
        c_curr = candles[i]
        h, l, c, o = c_curr["high"], c_curr["low"], c_curr["close"], c_curr["open"]
        rng = h - l
        if rng == 0:
            continue

        close_loc = (c - l) / rng
        
        # شرط 1: إغلاق متطرف وصاعد
        if close_loc >= 0.85 and c > o:
            body_ratio = (c - o) / rng
            if body_ratio >= 0.7:  # شمعة اندفاعية صلبة دون تراجع
                double_trap_total += 1
                
                next_lows = [candles[i+j]["low"] for j in range(1, 4)]
                if min(next_lows) < l:
                    double_trap_breakdown += 1

    print(f"Candles matching Double Condition (Extreme Close + Zero/Shallow Pullback): {double_trap_total}")
    if double_trap_total > 0:
        ratio = (double_trap_breakdown / double_trap_total) * 100
        print(f"Immediate Breakdown Rate for Double Condition: {double_trap_breakdown} ({ratio:.2f}%)")

    print("=" * 75)
    print("Diagnostic complete. No strategy files modified.")
    print("=" * 75)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
