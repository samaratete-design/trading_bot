"""
live/run_live_paper.py

Runs the FROZEN BTC Trend v1 strategy live, in PAPER mode, on real-time
BTC-USD 15m candles from Binance. Sends a Telegram message on every
signal, entry fill, and exit. Virtual $100 starting balance, accounting
only -- no real-money orders are possible (see
execution/paper_broker.py::PaperBroker.MODE, which is fixed to "PAPER").

Does NOT modify strategies/btc_trend_v1.py or any parameter. Reuses the
already-validated PaperBroker / BTCTrendV1Adapter / PositionStateMachine /
risk/position_sizer.py stack (Historical Runtime Consistency Gate: PASS,
179/179 trades bit-identical to walk_forward_full_dataset.py) and the
SQLite persistence layer, so the process can be killed and restarted
without losing or duplicating a trade -- that's existing, already-tested
infrastructure, not something new being added here.

Usage:
    export TELEGRAM_BOT_TOKEN=your_bot_token
    export TELEGRAM_CHAT_ID=your_chat_id
    python3 live/run_live_paper.py

Optional env vars:
    LIVE_DB_PATH     default: live_btc_trend_v1.sqlite
    POLL_SECONDS     default: 60
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from execution.state_machine import RuntimeState
from live.binance_feed import fetch_recent_closed_candles
from live.telegram_event_adapter import TelegramEventAdapter
from live.telegram_notifier import TelegramNotifier
from persistence.sqlite_store import PersistentPaperBroker

DB_PATH = os.environ.get("LIVE_DB_PATH", "live_btc_trend_v1.sqlite")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
STARTING_BALANCE = 100.0
SYMBOL = "BTC-USD"
WARMUP_CANDLES = 250  # strategy.MIN_WARMUP is 200; fetch a margin above it


def fmt(p: float) -> str:
    return f"{p:,.2f}"


def _process_one_candle(pb: PersistentPaperBroker, notifier: TelegramNotifier, candle, idx: int) -> None:
    """Thin wrapper kept for backward compatibility with earlier smoke
    tests -- all the actual event -> Telegram translation logic now lives
    in live/telegram_event_adapter.py, independently tested offline in
    tests/test_telegram_event_adapter.py."""
    TelegramEventAdapter(pb, notifier).process_candle(candle, idx)


def main() -> None:
    notifier = TelegramNotifier()

    if os.path.exists(DB_PATH):
        pb = PersistentPaperBroker.restore(DB_PATH)
        last = pb.get_last_candle()
        next_idx = last[0] + 1
        last_ts = last[1]
        notifier.send(
            f"\U0001F504 BTC Trend v1 PAPER bot RESTARTED\n"
            f"state={pb.broker.state.value}  balance=${pb.broker.balance:.2f}\n"
            f"resuming after candle {last_ts}"
        )
    else:
        pb = PersistentPaperBroker(DB_PATH, starting_balance=STARTING_BALANCE, symbol=SYMBOL)
        print(f"[warmup] fetching {WARMUP_CANDLES} closed candles for strategy indicator warmup...")
        warmup_candles = fetch_recent_closed_candles(limit=WARMUP_CANDLES)
        for idx, c in enumerate(warmup_candles):
            pb.process_closed_candle(c, idx)
        next_idx = len(warmup_candles)
        last_ts = warmup_candles[-1].timestamp
        print(f"[warmup] done -- {len(warmup_candles)} candles fed, last={last_ts}")
        notifier.send(
            f"\U0001F7E2 BTC Trend v1 PAPER bot STARTED\n"
            f"BTC-USD 15m | PAPER mode | virtual balance=${pb.broker.balance:.2f}\n"
            f"warmup complete through {last_ts}, watching for live signals now."
        )

    print(f"[live] entering poll loop, every {POLL_SECONDS}s, next_idx={next_idx}, last_ts={last_ts}")
    while True:
        try:
            recent = fetch_recent_closed_candles(limit=10)
        except Exception as e:
            print(f"[feed error] {e}")
            time.sleep(POLL_SECONDS)
            continue

        new_candles = sorted([c for c in recent if c.timestamp > last_ts], key=lambda c: c.timestamp)
        for c in new_candles:
            print(f"[candle] processing closed candle {c.timestamp} idx={next_idx}")
            _process_one_candle(pb, notifier, c, next_idx)
            last_ts = c.timestamp
            next_idx += 1

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
