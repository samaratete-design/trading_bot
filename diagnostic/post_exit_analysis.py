"""
diagnostic/post_exit_analysis.py

POST-EXIT ANALYSIS — STRUCTURE_STOP trades, BTC Trend v1
==========================================================
READ-ONLY diagnostic. Does not modify strategies/btc_trend_v1.py,
walk_forward_full_dataset.py, the dataset, fees, slippage, entry logic,
exit logic, or any parameter. No git operations performed.

Purpose: quantitatively separate Entry Quality from Stop Placement for
STRUCTURE_STOP trades in the frozen BTC Trend v1 baseline, by measuring
what price actually did AFTER the structure-stop exit (diagnostic only —
this information is never fed back into the original entry/exit decision).

Data source: fixtures/btc_15m_full.csv (the authoritative full-dataset
scope confirmed to reproduce the frozen baseline: 179 trades, 56
structure_stop, win rate 17.32%, Net PnL -69.9809).

METHOD (per protocol):
1. Import and run the EXISTING authoritative replay
   (walk_forward_full_dataset.run_baseline), unmodified, to get the
   authoritative 179 trades. This is the trade generation of record.
2. Run a second, local "extended replay" that duplicates the exact same
   strategy calls/order/thresholds as run_baseline, adding ONLY extra
   instrumentation (entry_price, initial_stop, stop_distance, exit_index,
   mae_r) that run_baseline does not expose. No decision logic differs.
3. Cross-validate: extended replay must match the authoritative replay
   exactly (same count, same structure_stop count, entry_time/exit_time/
   reason/net_pnl/exit_r/mfe_r identical to 1e-6 per trade, aligned by
   list order). If ANY mismatch -> STOP, print the discrepancy, do not
   proceed to post-exit analysis or interpretation.
4. Only after cross-validation passes: for each STRUCTURE_STOP trade,
   analyze candles strictly AFTER exit_index (exit_index+1 onward) --
   the exit candle itself is never included in post-exit measurement,
   to avoid contamination.
5. No lookahead is used to alter the original entry/exit decision --
   future candles are only read here, in this diagnostic, after the
   original trade record already exists.

Run: python3 diagnostic/post_exit_analysis.py
"""

import csv
import statistics
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "strategies")

from walk_forward_full_dataset import load_candles, run_baseline
from btc_trend_v1 import BTCTrendV1Strategy, PositionState

CSV_PATH = "fixtures/btc_15m_full.csv"
STARTING_BALANCE = 100.0
RISK_PCT = 0.02
FEE_RATE = 0.0006
SLIPPAGE_RATE = 0.0002

WINDOWS = [5, 10, 20, 40, 80]
R_LEVELS = [0.5, 1.0, 2.0, 3.0]
R_LEVEL_LABELS = {0.5: "0_5r", 1.0: "1r", 2.0: "2r", 3.0: "3r"}

OUT_CSV = "diagnostic/post_exit_analysis.csv"


# ---------------------------------------------------------------------------
# Step 2: extended replay -- identical logic to run_baseline, with extra
# instrumentation fields only. No decision/threshold/order changes.
# ---------------------------------------------------------------------------
def run_extended_replay(candles):
    strategy = BTCTrendV1Strategy()
    balance = STARTING_BALANCE
    position = None
    open_meta = None
    trades = []

    for idx, candle in enumerate(candles):
        strategy.on_closed_candle(candle)

        if position is not None:
            stop_distance = open_meta["entry_price"] - open_meta["initial_stop"]
            open_meta["lowest_low_since_entry"] = min(
                open_meta["lowest_low_since_entry"], candle.low
            )
            exit_instr = strategy.evaluate_exit(position)

            if exit_instr is not None:
                exit_price = exit_instr.exit_price * (1 - SLIPPAGE_RATE)
                gross_pnl = (exit_price - open_meta["entry_price"]) * open_meta["quantity"]
                exit_fee = exit_price * open_meta["quantity"] * FEE_RATE
                net_pnl = gross_pnl - exit_fee
                balance += net_pnl

                exit_r = (
                    (exit_price - open_meta["entry_price"]) / stop_distance
                    if stop_distance > 0 else 0.0
                )
                mfe_price = position.highest_high_since_entry
                mfe_r = (
                    (mfe_price - open_meta["entry_price"]) / stop_distance
                    if stop_distance > 0 else 0.0
                )
                mae_price = open_meta["lowest_low_since_entry"]
                mae_r = (
                    (mae_price - open_meta["entry_price"]) / stop_distance
                    if stop_distance > 0 else 0.0
                )

                trades.append({
                    "entry_index": open_meta["entry_index"],
                    "exit_index": idx,
                    "entry_time": str(open_meta["entry_time"]),
                    "exit_time": str(candle.timestamp),
                    "entry_price": open_meta["entry_price"],
                    "initial_stop": open_meta["initial_stop"],
                    "stop_distance": stop_distance,
                    "net_pnl": net_pnl,
                    "exit_r": exit_r,
                    "mfe_r": mfe_r,
                    "mae_r": mae_r,
                    "reason": exit_instr.reason.value,
                })

                position = None
                open_meta = None
                continue

        if position is None:
            signal = strategy.evaluate_entry()
            if signal is not None and idx + 1 < len(candles):
                next_candle = candles[idx + 1]
                raw_entry_price = next_candle.open
                entry_price = raw_entry_price * (1 + SLIPPAGE_RATE)
                stop_distance = entry_price - signal.initial_stop
                if stop_distance <= 0:
                    continue

                risk_amount = balance * RISK_PCT
                quantity = risk_amount / stop_distance
                entry_fee = entry_price * quantity * FEE_RATE
                balance -= entry_fee

                position = PositionState(
                    entry_price=entry_price,
                    structure_stop=signal.initial_stop,
                    highest_high_since_entry=next_candle.high,
                )
                open_meta = {
                    "entry_time": next_candle.timestamp,
                    "entry_price": entry_price,
                    "initial_stop": signal.initial_stop,
                    "quantity": quantity,
                    "risk_amount": risk_amount,
                    "entry_fee": entry_fee,
                    "lowest_low_since_entry": next_candle.low,
                    "entry_index": idx + 1,
                }

    return trades, balance


# ---------------------------------------------------------------------------
# Step 3: cross-validation
# ---------------------------------------------------------------------------
def cross_validate(authoritative, extended):
    if len(authoritative) != len(extended):
        return False, f"trade count mismatch: authoritative={len(authoritative)} extended={len(extended)}"

    for i, (a, e) in enumerate(zip(authoritative, extended)):
        if a["entry_time"] != e["entry_time"]:
            return False, f"trade {i}: entry_time mismatch ({a['entry_time']} vs {e['entry_time']})"
        if a["exit_time"] != e["exit_time"]:
            return False, f"trade {i}: exit_time mismatch ({a['exit_time']} vs {e['exit_time']})"
        if a["reason"] != e["reason"]:
            return False, f"trade {i}: reason mismatch ({a['reason']} vs {e['reason']})"
        if abs(a["net_pnl"] - e["net_pnl"]) > 1e-6:
            return False, f"trade {i}: net_pnl mismatch ({a['net_pnl']} vs {e['net_pnl']})"
        if abs(a["exit_r"] - e["exit_r"]) > 1e-6:
            return False, f"trade {i}: exit_r mismatch ({a['exit_r']} vs {e['exit_r']})"
        if abs(a["mfe_r"] - e["mfe_r"]) > 1e-6:
            return False, f"trade {i}: mfe_r mismatch ({a['mfe_r']} vs {e['mfe_r']})"

    a_structure = [t for t in authoritative if t["reason"] == "structure_stop"]
    e_structure = [t for t in extended if t["reason"] == "structure_stop"]
    if len(a_structure) != len(e_structure):
        return False, f"structure_stop count mismatch: authoritative={len(a_structure)} extended={len(e_structure)}"

    return True, None


# ---------------------------------------------------------------------------
# Step 4-5: post-exit measurement
# ---------------------------------------------------------------------------
def analyze_post_exit(trade, candles):
    exit_index = trade["exit_index"]
    entry_price = trade["entry_price"]
    stop_distance = trade["stop_distance"]
    n_candles = len(candles)

    result = {}
    for w in WINDOWS:
        window_start = exit_index + 1
        window_end = min(exit_index + w, n_candles - 1)  # inclusive index cap
        window_candles = candles[window_start:window_end + 1]
        available = len(window_candles)

        if available == 0:
            result[w] = {
                "post_exit_mfe_r": None,
                "post_exit_mae_r": None,
                "recovered_entry": False,
                "hits": {lvl: False for lvl in R_LEVELS},
                "bars_to": {lvl: None for lvl in R_LEVELS},
                "available_candles": 0,
                "truncated": True,
            }
            continue

        mfe_r = float("-inf")
        mae_r = float("inf")
        recovered_entry = False
        hits = {lvl: False for lvl in R_LEVELS}
        bars_to = {lvl: None for lvl in R_LEVELS}

        for bar_offset, c in enumerate(window_candles, start=1):
            high_r = (c.high - entry_price) / stop_distance
            low_r = (c.low - entry_price) / stop_distance
            mfe_r = max(mfe_r, high_r)
            mae_r = min(mae_r, low_r)
            if c.high >= entry_price:
                recovered_entry = True
            for lvl in R_LEVELS:
                if not hits[lvl] and high_r >= lvl:
                    hits[lvl] = True
                    bars_to[lvl] = bar_offset

        result[w] = {
            "post_exit_mfe_r": mfe_r,
            "post_exit_mae_r": mae_r,
            "recovered_entry": recovered_entry,
            "hits": hits,
            "bars_to": bars_to,
            "available_candles": available,
            "truncated": available < w,
        }
    return result


def pct(n, d):
    return (100.0 * n / d) if d else 0.0


def bucket_mfe(v):
    if v is None:
        return "N/A"
    if v < 0:
        return "<0"
    if v < 0.5:
        return "0-0.5R"
    if v < 1.0:
        return "0.5-1R"
    if v < 2.0:
        return "1-2R"
    if v < 3.0:
        return "2-3R"
    return ">=3R"


def main():
    print("Loading candles from", CSV_PATH)
    candles = load_candles(CSV_PATH)
    print(f"Loaded {len(candles)} candles")

    print()
    print("Running AUTHORITATIVE replay (walk_forward_full_dataset.run_baseline, unmodified)...")
    authoritative_trades, _ = run_baseline(candles)

    print("Running EXTENDED replay (same logic + instrumentation)...")
    extended_trades, _ = run_extended_replay(candles)

    ok, reason = cross_validate(authoritative_trades, extended_trades)
    print()
    print("=" * 66)
    print("CROSS-VALIDATION")
    print("=" * 66)
    print(f"Authoritative trades: {len(authoritative_trades)}")
    print(f"Extended trades:      {len(extended_trades)}")
    if not ok:
        print(f"MISMATCH: {reason}")
        print()
        print("STOPPING per protocol. No post-exit analysis performed.")
        return
    print("Result: MATCH (entry_time/exit_time/reason/net_pnl/exit_r/mfe_r identical to 1e-6)")

    structure_trades = [t for t in extended_trades if t["reason"] == "structure_stop"]
    print(f"Baseline trades: {len(authoritative_trades)}")
    print(f"Structure-stop trades: {len(structure_trades)}")

    if len(authoritative_trades) != 179 or len(structure_trades) != 56:
        print()
        print(f"MISMATCH vs expected baseline (179 total / 56 structure_stop).")
        print("STOPPING per protocol. No post-exit analysis performed.")
        return

    print(f"First structure_stop trade: {structure_trades[0]['entry_time']} -> {structure_trades[0]['exit_time']}")
    print(f"Last structure_stop trade:  {structure_trades[-1]['entry_time']} -> {structure_trades[-1]['exit_time']}")

    # --- Post-exit analysis ---
    per_trade_results = []
    for t in structure_trades:
        pe = analyze_post_exit(t, candles)
        per_trade_results.append((t, pe))

    truncated_any = any(
        pe[w]["truncated"] for _, pe in per_trade_results for w in WINDOWS
    )

    # --- CSV output ---
    fieldnames = [
        "entry_time", "exit_time", "exit_index", "entry_price", "initial_stop",
        "risk_distance", "exit_r", "mfe_r", "mae_r",
    ]
    for w in WINDOWS:
        fieldnames.append(f"post_exit_mfe_{w}_r")
        fieldnames.append(f"post_exit_mae_{w}_r")
        fieldnames.append(f"recovered_entry_{w}")
        for lvl in R_LEVELS:
            lbl = R_LEVEL_LABELS[lvl]
            fieldnames.append(f"hit_{lbl}_{w}")
        for lvl in R_LEVELS:
            lbl = R_LEVEL_LABELS[lvl]
            fieldnames.append(f"bars_to_{lbl}_{w}")

    with open(OUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t, pe in per_trade_results:
            row = {
                "entry_time": t["entry_time"],
                "exit_time": t["exit_time"],
                "exit_index": t["exit_index"],
                "entry_price": t["entry_price"],
                "initial_stop": t["initial_stop"],
                "risk_distance": t["stop_distance"],
                "exit_r": t["exit_r"],
                "mfe_r": t["mfe_r"],
                "mae_r": t["mae_r"],
            }
            for w in WINDOWS:
                d = pe[w]
                row[f"post_exit_mfe_{w}_r"] = d["post_exit_mfe_r"]
                row[f"post_exit_mae_{w}_r"] = d["post_exit_mae_r"]
                row[f"recovered_entry_{w}"] = d["recovered_entry"]
                for lvl in R_LEVELS:
                    lbl = R_LEVEL_LABELS[lvl]
                    row[f"hit_{lbl}_{w}"] = d["hits"][lvl]
                    row[f"bars_to_{lbl}_{w}"] = d["bars_to"][lvl]
            writer.writerow(row)

    print()
    print(f"Per-trade CSV written: {OUT_CSV} ({len(per_trade_results)} rows)")
    if truncated_any:
        print("NOTE: at least one trade had a truncated window (insufficient future candles).")
    else:
        print("NOTE: no window truncation occurred (sufficient future candles for all trades/windows).")

    # --- Aggregate report ---
    n = len(structure_trades)
    print()
    print("=" * 66)
    print("POST-EXIT ANALYSIS — STRUCTURE STOP")
    print("=" * 66)
    print()
    print(f"Baseline trades: {len(authoritative_trades)}")
    print(f"Structure-stop trades: {n}")
    print()

    header = f"{'WINDOW':<10}{'N':>5}{'RECOVER ENTRY %':>18}{'HIT +0.5R %':>14}{'HIT +1R %':>12}{'HIT +2R %':>12}{'HIT +3R %':>12}"
    print(header)
    print("-" * len(header))
    agg = {}
    for w in WINDOWS:
        recov = sum(1 for _, pe in per_trade_results if pe[w]["recovered_entry"])
        hit05 = sum(1 for _, pe in per_trade_results if pe[w]["hits"][0.5])
        hit1 = sum(1 for _, pe in per_trade_results if pe[w]["hits"][1.0])
        hit2 = sum(1 for _, pe in per_trade_results if pe[w]["hits"][2.0])
        hit3 = sum(1 for _, pe in per_trade_results if pe[w]["hits"][3.0])
        agg[w] = dict(recov=recov, hit05=hit05, hit1=hit1, hit2=hit2, hit3=hit3)
        print(f"{w:<10}{n:>5}{pct(recov, n):>17.1f}%{pct(hit05, n):>13.1f}%{pct(hit1, n):>11.1f}%{pct(hit2, n):>11.1f}%{pct(hit3, n):>11.1f}%")

    print()
    print("=" * 66)
    print("POST-EXIT MFE DISTRIBUTION")
    print("=" * 66)
    bucket_labels = ["<0", "0-0.5R", "0.5-1R", "1-2R", "2-3R", ">=3R"]
    for w in WINDOWS:
        values = [pe[w]["post_exit_mfe_r"] for _, pe in per_trade_results if pe[w]["post_exit_mfe_r"] is not None]
        print()
        print(f"{w} candles (n={len(values)}):")
        if not values:
            print("  no data")
            continue
        mean_v = statistics.mean(values)
        median_v = statistics.median(values)
        sorted_v = sorted(values)
        p75 = sorted_v[min(len(sorted_v) - 1, int(round(0.75 * (len(sorted_v) - 1))))]
        p90 = sorted_v[min(len(sorted_v) - 1, int(round(0.90 * (len(sorted_v) - 1))))]
        max_v = max(values)
        print(f"  mean   {mean_v:.4f}")
        print(f"  median {median_v:.4f}")
        print(f"  P75    {p75:.4f}")
        print(f"  P90    {p90:.4f}")
        print(f"  max    {max_v:.4f}")
        counts = {b: 0 for b in bucket_labels}
        for v in values:
            counts[bucket_mfe(v)] += 1
        print("  buckets: " + ", ".join(f"{b}={counts[b]}" for b in bucket_labels))

    # --- Engineering interpretation (numbers only, no strategy recommendation) ---
    print()
    print("=" * 66)
    print("ENGINEERING INTERPRETATION")
    print("=" * 66)
    w40 = agg.get(40, agg[WINDOWS[-1]])
    w20 = agg.get(20, agg[WINDOWS[-1]])
    hit1_40_pct = pct(w40["hit1"], n)
    hit2_40_pct = pct(w40["hit2"], n)
    recov_40_pct = pct(w40["recov"], n)
    hit1_20_pct = pct(w20["hit1"], n)

    print(f"n = {n} structure_stop trades.")
    print(f"Within 40 candles post-exit: recovered-to-entry = {recov_40_pct:.1f}%, "
          f"reached +1R = {hit1_40_pct:.1f}%, reached +2R = {hit2_40_pct:.1f}%.")
    print(f"Within 20 candles post-exit: reached +1R = {hit1_20_pct:.1f}%.")
    print()

    if hit2_40_pct >= 30:
        verdict = 1
        verdict_label = "Strong evidence supporting Stop Placement problem"
    elif hit1_40_pct >= 30:
        verdict = 2
        verdict_label = "Moderate evidence"
    elif hit1_40_pct >= 15 or recov_40_pct >= 40:
        verdict = 3
        verdict_label = "Weak evidence"
    elif recov_40_pct < 15:
        verdict = 4
        verdict_label = "Evidence against Stop Placement problem"
    else:
        verdict = 5
        verdict_label = "Inconclusive"

    print(f"Classification: {verdict}. {verdict_label}")
    print(f"Basis (numbers only): {hit2_40_pct:.1f}% of structure_stop trades reached +2R within 40 "
          f"candles post-exit, {hit1_40_pct:.1f}% reached +1R, {recov_40_pct:.1f}% merely recovered to entry "
          f"(0R) without necessarily reaching +1R. Simple recovery-to-entry alone is not treated as evidence "
          f"of a stop-placement problem; the classification above is driven by the +1R/+2R meaningful-"
          f"continuation rates.")
    print("=" * 66)


if __name__ == "__main__":
    main()
