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
    LIVE_DATA_FEED   default: binance  (also accepts: yahoo)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from execution.state_machine import RuntimeState
from live.binance_feed import fetch_recent_closed_candles
from live.telegram_event_adapter import TelegramEventAdapter
from live.telegram_notifier import TelegramNotifier
from live.yahoo_feed import YahooFeed
from persistence.sqlite_store import PersistentPaperBroker

DB_PATH = os.environ.get("LIVE_DB_PATH", "live_btc_trend_v1.sqlite")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
STARTING_BALANCE = 100.0
SYMBOL = "BTC-USD"
WARMUP_CANDLES = 250  # strategy.MIN_WARMUP is 200; fetch a margin above it


class BinanceFeedAdapter:
    """Thin wrapper around the EXISTING live/binance_feed.py module-level
    function so it exposes the same shape as YahooFeed (.symbol,
    .interval, .fetch_closed_candles(limit)) for get_data_feed() to
    return uniformly. live/binance_feed.py itself is untouched -- this
    is the default feed and remains exactly as-is (LIVE_DATA_FEED=binance
    or unset)."""

    symbol = "BTCUSDT"
    interval = "15m"

    def fetch_closed_candles(self, limit: int = 250):
        return fetch_recent_closed_candles(limit=limit)


def get_data_feed():
    """Selects the live candle feed via LIVE_DATA_FEED (default: binance).

    This is the SAME feed instance used for warmup, polling, and
    restart/resume in main() below -- not a separate code path.
    """
    feed_name = os.environ.get("LIVE_DATA_FEED", "binance").strip().lower()
    if feed_name == "binance":
        return BinanceFeedAdapter()
    if feed_name == "yahoo":
        return YahooFeed(symbol="BTC-USD", interval="15m")
    raise ValueError(
        f"Unknown LIVE_DATA_FEED={feed_name!r} (expected 'binance' or 'yahoo')"
    )


def _coerce_last_ts(feed, last_ts):
    """persistence/sqlite_store.py always persists candle.timestamp as
    TEXT (str(candle.timestamp)) and always reads it back as a plain str
    on restore (see PersistentPaperBroker.restore /
    PersistentPaperBroker.get_last_candle) -- that part of the persistence
    layer is unchanged here, per instruction not to rewrite existing
    architecture.

    On the warmup (non-restart) path, last_ts is already whatever type
    this feed's own Candle.timestamp uses (str for Binance, datetime for
    Yahoo) -- nothing to coerce there; str.fromisoformat would be a no-op
    on that path anyway since it's not called with restart-read TEXT.

    On the restart path, last_ts is always plain TEXT regardless of feed.
    live/binance_feed.py's candle.timestamp is itself already a string, so
    `str > str` continues to work exactly as before for
    LIVE_DATA_FEED=binance -- no behavior change.

    live/yahoo_feed.py's candle.timestamp is a real datetime (per the
    core.models.Candle contract), so after a restart the persisted TEXT
    value has to be parsed back into a datetime before it can be compared
    against newly-fetched Yahoo candles in the poll loop below; otherwise
    `datetime > str` raises TypeError the first time the process resumes.
    This function does only that reconciliation, scoped to this file.
    """
    if isinstance(feed, YahooFeed) and isinstance(last_ts, str):
        return datetime.fromisoformat(last_ts)
    return last_ts


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
    feed = get_data_feed()
    print(f"[feed] using {type(feed).__name__} ({feed.symbol}, {feed.interval})")

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
        warmup_candles = feed.fetch_closed_candles(limit=WARMUP_CANDLES)
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

    # last_ts is only ever plain TEXT the moment it came from
    # pb.get_last_candle() (restart path) -- reconcile it to this feed's
    # actual candle.timestamp type (see _coerce_last_ts docstring) before
    # the poll loop below compares them with `>`.
    last_ts = _coerce_last_ts(feed, last_ts)

    print(f"[live] entering poll loop, every {POLL_SECONDS}s, next_idx={next_idx}, last_ts={last_ts}")
    while True:
        try:
            recent = feed.fetch_closed_candles(limit=10)
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
