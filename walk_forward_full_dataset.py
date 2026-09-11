"""
walk_forward_full_dataset.py

Runs the SAME corrected methodology as walk_forward_pullback_test.py
(single Baseline execution path; Test D derived as a strict post-hoc
subset; assertions enforcing the subset invariant) on the expanded
6-12 month dataset, then mechanically applies the pre-registered
acceptance criteria agreed before this data was fetched:

  §2  Block count: 6 blocks if Baseline trades >= 120, else 4 blocks
      (only these two options were pre-registered; if trade count falls
      outside both expectations, the script says so and stops rather
      than silently picking a number).
  §3  Minimum sample gate: Test D needs >=30 full-sample trades before
      PF/win-rate are treated as meaningful. Below that: labeled
      "INSUFFICIENT SAMPLE", never "accepted" or "rejected".
  §4  Consistency gate: PF > 1.0 in >=75% of blocks that have trades,
      AND net PnL positive in >=60% of blocks.
  §5  Concentration gate: largest single trade <=25% of total positive
      net PnL; largest single block <=50% of total positive net PnL.

No threshold, EMA/ATR/ADX period, entry rule, exit rule, or risk
parameter is changed here. strategies/btc_trend_v1.py is not touched.
The gates are applied exactly as agreed, before this run, with no
re-interpretation after seeing the numbers.

Usage:
    python3 walk_forward_full_dataset.py --csv fixtures/btc_15m_full.csv
"""

import argparse
import csv
import sys

sys.path.insert(0, "strategies")
from btc_trend_v1 import (
    BTCTrendV1Strategy,
    Candle,
    PositionState,
    ema_series,
    atr_series,
)

STARTING_BALANCE = 100.0
RISK_PCT = 0.02
FEE_RATE = 0.0006
SLIPPAGE_RATE = 0.0002

DEPTH_MIN = 0.50
CLOSE_LOC_MIN = 0.50
CLOSE_LOC_MAX = 0.70

# --- Pre-registered acceptance criteria (§2-§5), fixed BEFORE this run ---
BLOCK_COUNT_HIGH = 6
BLOCK_COUNT_LOW = 4
BLOCK_COUNT_HIGH_THRESHOLD = 120  # baseline trades >= this -> 6 blocks
MIN_SAMPLE_TRADES = 30            # §3
CONSISTENCY_PF_BLOCK_FRACTION = 0.75   # §4
CONSISTENCY_NET_BLOCK_FRACTION = 0.60  # §4
MAX_SINGLE_TRADE_SHARE = 0.25   # §5
MAX_SINGLE_BLOCK_SHARE = 0.50   # §5


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=str, default="fixtures/btc_15m_full.csv")
    return p.parse_args()


def load_candles(path):
    candles = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append(
                Candle(
                    timestamp=row["timestamp"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return candles


def passes_test_d(candle, ema20_val, atr_val):
    if ema20_val is None or atr_val is None or atr_val <= 0:
        return False
    depth = (ema20_val - candle.low) / atr_val
    hl_range = candle.high - candle.low
    close_loc = (candle.close - candle.low) / hl_range if hl_range > 0 else 0.5
    return (depth >= DEPTH_MIN) and (CLOSE_LOC_MIN <= close_loc < CLOSE_LOC_MAX)


def run_baseline(candles):
    strategy = BTCTrendV1Strategy()
    closes = [c.close for c in candles]
    ema20_full = ema_series(closes, 20)
    atr_full = atr_series(candles, 14)

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

                sig_i = open_meta["signal_index"]
                sig_candle = candles[sig_i]
                e20 = ema20_full[sig_i]
                a = atr_full[sig_i]
                test_d_passed = passes_test_d(sig_candle, e20, a)

                trades.append({
                    "entry_index": open_meta["entry_index"],
                    "signal_index": sig_i,
                    "entry_time": str(open_meta["entry_time"]),
                    "exit_time": str(candle.timestamp),
                    "net_pnl": net_pnl,
                    "exit_r": exit_r,
                    "mfe_r": mfe_r,
                    "reason": exit_instr.reason.value,
                    "test_d_passed": test_d_passed,
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
                stop_distance = entry_price - signal.stop_loss
                if stop_distance <= 0:
                    continue

                risk_amount = balance * RISK_PCT
                quantity = risk_amount / stop_distance
                entry_fee = entry_price * quantity * FEE_RATE
                balance -= entry_fee

                position = PositionState(
                    entry_price=entry_price,
                    structure_stop=signal.stop_loss,
                    highest_high_since_entry=next_candle.high,
                )
                open_meta = {
                    "entry_time": next_candle.timestamp,
                    "entry_price": entry_price,
                    "initial_stop": signal.stop_loss,
                    "quantity": quantity,
                    "risk_amount": risk_amount,
                    "entry_fee": entry_fee,
                    "lowest_low_since_entry": next_candle.low,
                    "entry_index": idx + 1,
                    "signal_index": idx,
                }

    return trades, balance


def max_drawdown_pct(equity_curve):
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (v - peak) / peak
            max_dd = min(max_dd, dd)
    return max_dd * 100


def compute_stats(trades, starting_balance=STARTING_BALANCE):
    n = len(trades)
    if n == 0:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "profit_factor": None,
                "net_pnl": 0.0, "max_dd": 0.0}
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    win_rate = 100 * len(wins) / n
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    net = sum(t["net_pnl"] for t in trades)
    equity_curve = [starting_balance]
    running = starting_balance
    for t in trades:
        running += t["net_pnl"]
        equity_curve.append(running)
    dd = max_drawdown_pct(equity_curve)
    return {"trades": n, "wins": len(wins), "win_rate": win_rate,
            "profit_factor": profit_factor, "net_pnl": net, "max_dd": dd}


def fmt_pf(pf):
    if pf is None:
        return "N/A"
    if pf == float("inf"):
        return "inf"
    return f"{pf:.4f}"


def decide_block_count(baseline_trade_count):
    if baseline_trade_count >= BLOCK_COUNT_HIGH_THRESHOLD:
        return BLOCK_COUNT_HIGH, "trades >= 120"
    elif baseline_trade_count >= 60:
        return BLOCK_COUNT_LOW, "60 <= trades < 120"
    else:
        return None, "trades < 60 (outside pre-registered expectations)"


def apply_gates(testd_trades, block_bounds, entry_index_key="entry_index"):
    """Mechanically applies §3, §4, §5. Returns a dict verdict -- no
    judgment calls made outside what was pre-registered."""
    n = len(testd_trades)

    # §3
    if n < MIN_SAMPLE_TRADES:
        return {
            "verdict": "INSUFFICIENT_SAMPLE",
            "reason": f"Test D has {n} trades, below the pre-registered "
                      f"minimum of {MIN_SAMPLE_TRADES}.",
        }

    # Per-block stats for §4
    block_stats = []
    for (start, end) in block_bounds:
        b_trades = [t for t in testd_trades if start <= t[entry_index_key] < end]
        block_stats.append(compute_stats(b_trades))

    blocks_with_trades = [s for s in block_stats if s["trades"] > 0]
    if not blocks_with_trades:
        return {"verdict": "INSUFFICIENT_SAMPLE",
                "reason": "No block contains any Test D trades."}

    blocks_pf_gt_1 = [s for s in blocks_with_trades
                       if s["profit_factor"] is not None and s["profit_factor"] > 1.0]
    blocks_net_positive = [s for s in block_stats if s["net_pnl"] > 0]

    pf_fraction = len(blocks_pf_gt_1) / len(blocks_with_trades)
    net_fraction = len(blocks_net_positive) / len(block_stats)

    consistency_pass = (
        pf_fraction >= CONSISTENCY_PF_BLOCK_FRACTION
        and net_fraction >= CONSISTENCY_NET_BLOCK_FRACTION
    )

    # §5 concentration
    positive_trades = [t for t in testd_trades if t["net_pnl"] > 0]
    total_positive_pnl = sum(t["net_pnl"] for t in positive_trades)

    largest_trade_share = None
    largest_block_share = None
    concentration_pass = True

    if total_positive_pnl > 0:
        largest_trade_pnl = max((t["net_pnl"] for t in positive_trades), default=0.0)
        largest_trade_share = largest_trade_pnl / total_positive_pnl

        block_positive_pnls = []
        for (start, end) in block_bounds:
            b_pos = sum(t["net_pnl"] for t in testd_trades
                        if start <= t[entry_index_key] < end and t["net_pnl"] > 0)
            block_positive_pnls.append(b_pos)
        largest_block_pnl = max(block_positive_pnls, default=0.0)
        largest_block_share = largest_block_pnl / total_positive_pnl

        concentration_pass = (
            largest_trade_share <= MAX_SINGLE_TRADE_SHARE
            and largest_block_share <= MAX_SINGLE_BLOCK_SHARE
        )
    else:
        concentration_pass = False  # no positive PnL at all -> nothing to accept

    verdict = "ACCEPTED_AS_PRELIMINARY_EDGE" if (consistency_pass and concentration_pass) else "REJECTED"

    return {
        "verdict": verdict,
        "pf_fraction": pf_fraction,
        "net_fraction": net_fraction,
        "consistency_pass": consistency_pass,
        "largest_trade_share": largest_trade_share,
        "largest_block_share": largest_block_share,
        "concentration_pass": concentration_pass,
        "block_stats": block_stats,
    }


def main():
    args = parse_args()
    candles = load_candles(args.csv)
    n_candles = len(candles)
    print(f"Loaded {n_candles} candles from {args.csv}")

    baseline_trades, baseline_final = run_baseline(candles)
    testd_trades = [t for t in baseline_trades if t["test_d_passed"]]

    baseline_entry_times = {t["entry_time"] for t in baseline_trades}
    testd_entry_times = {t["entry_time"] for t in testd_trades}
    assert len(testd_trades) <= len(baseline_trades)
    assert testd_entry_times.issubset(baseline_entry_times)
    print(f"[invariant check passed] Test D ({len(testd_trades)}) subset of Baseline ({len(baseline_trades)})")

    print()
    print("=" * 78)
    print("FULL SAMPLE")
    print("=" * 78)
    print(f"Baseline trades: {len(baseline_trades)}")
    print(f"Test D trades:   {len(testd_trades)}")
    b_stats = compute_stats(baseline_trades)
    d_stats = compute_stats(testd_trades)
    print(f"\nBaseline: WR {b_stats['win_rate']:.2f}%  PF {fmt_pf(b_stats['profit_factor'])}  "
          f"Net {b_stats['net_pnl']:+.4f}  MaxDD {b_stats['max_dd']:.2f}%")
    print(f"Test D:   WR {d_stats['win_rate']:.2f}%  PF {fmt_pf(d_stats['profit_factor'])}  "
          f"Net {d_stats['net_pnl']:+.4f}  MaxDD {d_stats['max_dd']:.2f}%")

    n_blocks, reason = decide_block_count(len(baseline_trades))
    print()
    print(f"§2 block count decision: {reason}")
    if n_blocks is None:
        print("Baseline trade count falls outside the pre-registered 6/4 block")
        print("expectations. STOPPING per protocol -- block count must be")
        print("re-negotiated explicitly before proceeding, not chosen ad hoc.")
        return
    print(f"-> using {n_blocks} blocks")

    block_size = n_candles // n_blocks
    block_bounds = []
    for b in range(n_blocks):
        start = b * block_size
        end = (b + 1) * block_size if b < n_blocks - 1 else n_candles
        block_bounds.append((start, end))

    print()
    print("=" * 78)
    print(f"WALK-FORWARD BLOCKS ({n_blocks})")
    print("=" * 78)
    for i, (start, end) in enumerate(block_bounds, start=1):
        b_base = [t for t in baseline_trades if start <= t["entry_index"] < end]
        b_test = [t for t in testd_trades if start <= t["entry_index"] < end]
        bs = compute_stats(b_base)
        ds = compute_stats(b_test)
        print(f"\nBLOCK {i} (idx {start}-{end-1})  Baseline:{len(b_base)} Test D:{len(b_test)}")
        print(f"  Baseline  WR {bs['win_rate']:>6.2f}%  PF {fmt_pf(bs['profit_factor']):>8}  Net {bs['net_pnl']:>10.4f}  MaxDD {bs['max_dd']:>7.2f}%")
        print(f"  Test D    WR {ds['win_rate']:>6.2f}%  PF {fmt_pf(ds['profit_factor']):>8}  Net {ds['net_pnl']:>10.4f}  MaxDD {ds['max_dd']:>7.2f}%")

    print()
    print("=" * 78)
    print("PRE-REGISTERED ACCEPTANCE GATES (§3/§4/§5) — applied mechanically")
    print("=" * 78)
    gate_result = apply_gates(testd_trades, block_bounds)
    print(f"Verdict: {gate_result['verdict']}")
    if gate_result["verdict"] == "INSUFFICIENT_SAMPLE":
        print(f"Reason: {gate_result['reason']}")
    else:
        print(f"§4 PF>1.0 in blocks-with-trades: {gate_result['pf_fraction']*100:.1f}% "
              f"(need >= {CONSISTENCY_PF_BLOCK_FRACTION*100:.0f}%)")
        print(f"§4 Net-positive block fraction:  {gate_result['net_fraction']*100:.1f}% "
              f"(need >= {CONSISTENCY_NET_BLOCK_FRACTION*100:.0f}%)")
        print(f"§4 consistency gate: {'PASS' if gate_result['consistency_pass'] else 'FAIL'}")
        if gate_result["largest_trade_share"] is not None:
            print(f"§5 largest single trade share of positive PnL: "
                  f"{gate_result['largest_trade_share']*100:.1f}% "
                  f"(max allowed {MAX_SINGLE_TRADE_SHARE*100:.0f}%)")
            print(f"§5 largest single block share of positive PnL: "
                  f"{gate_result['largest_block_share']*100:.1f}% "
                  f"(max allowed {MAX_SINGLE_BLOCK_SHARE*100:.0f}%)")
        print(f"§5 concentration gate: {'PASS' if gate_result['concentration_pass'] else 'FAIL'}")
    print("=" * 78)


if __name__ == "__main__":
    main()
