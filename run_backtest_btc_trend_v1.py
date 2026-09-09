"""
run_backtest_btc_trend_v1.py

FIRST REAL BACKTEST of strategies/btc_trend_v1.py on the FROZEN snapshot
(fixtures/btc_15m_snapshot.csv). Does NOT touch runtime/engine.py or
execution/broker_interface.py — this is a standalone script with its own
simplified broker, built only to get a first honest read on how this
strategy's signals behave on real BTC data.

IMPORTANT — what this IS and IS NOT:
- IS: a first honest look at signal frequency/quality on real data, with
  fee_rate=0.0006 and slippage_rate=0.0002 applied (same constants as the
  old V6.5.2 baseline) so the numbers aren't misleadingly clean.
- IS NOT: the final PaperTradingBroker. Exit-side slippage/fee handling
  here is a REASONABLE APPROXIMATION for this preliminary read, not a
  ratified design — the real broker rebuild (gap handling, EOD, full
  ledger fidelity) is still a separate, later task.
- IS NOT: parity-tested against anything. This has no baseline to compare
  against because the strategy itself is new (v6.5.2 is retired). This
  script's only job is: does this new strategy do anything sane on real
  BTC-USD-proxy data.
- IS NOT: proof of profitability. One 60-day window on one symbol is a
  single data point, not statistical evidence either way.

Run: python3 run_backtest_btc_trend_v1.py
"""

import csv
import sys
from dataclasses import dataclass
from datetime import datetime

sys.path.insert(0, "strategies")
from btc_trend_v1 import (
    BTCTrendV1Strategy,
    Candle,
    PositionState,
)

STARTING_BALANCE = 100.0
RISK_PCT = 0.02
FEE_RATE = 0.0006
SLIPPAGE_RATE = 0.0002

CSV_PATH = "fixtures/btc_15m_snapshot.csv"


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    direction: str
    entry_price: float
    exit_price: float
    quantity: float
    net_pnl: float
    total_fees: float
    risk_amount: float
    r_multiple: float
    reason: str
    balance_after: float


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


def run_backtest(candles):
    strategy = BTCTrendV1Strategy()
    balance = STARTING_BALANCE
    position = None
    open_meta = None  # dict holding entry-time bookkeeping for the current open trade
    trades = []
    equity_curve = [balance]

    for idx, candle in enumerate(candles):
        strategy.on_closed_candle(candle)

        if position is not None:
            exit_instr = strategy.evaluate_exit(position)
            if exit_instr is not None:
                raw_exit_price = exit_instr.exit_price
                # Approximate exit slippage (sell side): reduces realized price.
                exit_price = raw_exit_price * (1 - SLIPPAGE_RATE)

                gross_pnl = (exit_price - open_meta["entry_price"]) * open_meta["quantity"]
                exit_fee = exit_price * open_meta["quantity"] * FEE_RATE
                total_fees = open_meta["entry_fee"] + exit_fee
                net_pnl = gross_pnl - exit_fee  # entry fee already deducted from balance at entry

                balance += net_pnl
                stop_distance = open_meta["entry_price"] - open_meta["initial_stop"]
                r_multiple = (
                    (exit_price - open_meta["entry_price"]) / stop_distance
                    if stop_distance > 0
                    else 0.0
                )

                trades.append(
                    Trade(
                        entry_time=str(open_meta["entry_time"]),
                        exit_time=str(candle.timestamp),
                        direction="BUY",
                        entry_price=open_meta["entry_price"],
                        exit_price=exit_price,
                        quantity=open_meta["quantity"],
                        net_pnl=net_pnl,
                        total_fees=total_fees,
                        risk_amount=open_meta["risk_amount"],
                        r_multiple=r_multiple,
                        reason=exit_instr.reason.value,
                        balance_after=balance,
                    )
                )
                equity_curve.append(balance)
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
                    # Degenerate case: stop is not below entry after slippage.
                    # Skip this signal rather than open a nonsensical position.
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
                }

    return trades, balance, equity_curve


def max_drawdown_pct(equity_curve):
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            dd = (v - peak) / peak
            max_dd = min(max_dd, dd)
    return max_dd * 100


def print_report(trades, final_balance):
    n = len(trades)
    print("=" * 60)
    print(f"BTC Trend v1 — first real backtest on frozen snapshot")
    print(f"Data: {CSV_PATH}")
    print("=" * 60)

    if n == 0:
        print("No trades were generated on this data window.")
        print(f"Final balance: {final_balance:.4f} (unchanged, no trades)")
        print("=" * 60)
        return

    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    win_rate = 100 * len(wins) / n

    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    net = sum(t.net_pnl for t in trades)
    expectancy = net / n
    total_fees = sum(t.total_fees for t in trades)

    equity_curve = [STARTING_BALANCE] + [t.balance_after for t in trades]
    dd = max_drawdown_pct(equity_curve)

    print(f"Trades: {n}")
    print(f"Win rate: {win_rate:.2f}%")
    print(f"Profit factor: {profit_factor:.4f}")
    print(f"Expectancy per trade: {expectancy:+.4f}")
    print(f"Net PnL: {net:+.4f}")
    print(f"Max drawdown: {dd:.2f}%")
    print(f"Total fees: {total_fees:.4f}")
    print(f"Starting balance: {STARTING_BALANCE:.4f}")
    print(f"Final balance: {final_balance:.4f}")
    print()
    print("Exit reason breakdown:")
    reason_counts = {}
    for t in trades:
        reason_counts[t.reason] = reason_counts.get(t.reason, 0) + 1
    for reason, count in sorted(reason_counts.items()):
        print(f"  {reason}: {count}")
    print()
    print("Per-trade ledger:")
    print(
        f"{'entry_time':<26}{'exit_time':<26}{'entry':>10}{'exit':>10}"
        f"{'qty':>10}{'net_pnl':>10}{'R':>7}{'reason':>16}"
    )
    for t in trades:
        print(
            f"{t.entry_time:<26}{t.exit_time:<26}{t.entry_price:>10.2f}"
            f"{t.exit_price:>10.2f}{t.quantity:>10.4f}{t.net_pnl:>10.4f}"
            f"{t.r_multiple:>7.2f}{t.reason:>16}"
        )
    print("=" * 60)


def main():
    candles = load_candles(CSV_PATH)
    print(f"Loaded {len(candles)} candles from {CSV_PATH}")
    trades, final_balance, equity_curve = run_backtest(candles)
    print_report(trades, final_balance)


if __name__ == "__main__":
    main()
