"""
live/telegram_event_adapter.py

Pure event -> Telegram translation layer. Owns NO strategy logic, NO
sizing logic, NO persistence/replay mechanics -- all of that is the
already-built, already-tested PersistentPaperBroker (which itself wraps
PaperBroker / BTCTrendV1Adapter / PositionStateMachine /
risk/position_sizer.py, none of which are touched here). This module only
watches for state transitions after each processed candle and turns them
into a Telegram notification. It is deliberately separated from
live/run_live_paper.py (which only does Binance polling / orchestration)
so it can be fully unit-tested offline: no network, no real Telegram
token, no Binance call -- see tests/test_telegram_event_adapter.py.

Three events are recognized, each fired at most once per candle:
  - SIGNAL       : a new pending entry appeared this candle (signal candle
                   N closed; fill is still pending at N+1's open)
  - ENTRY FILLED : state transitioned FLAT -> LONG this candle
  - EXIT         : a new closed trade appeared this candle (state
                   transitioned LONG -> FLAT); WIN/LOSS is net_pnl > 0

No message is sent on a candle where nothing changed.
"""
from __future__ import annotations

from execution.state_machine import RuntimeState
from live.telegram_notifier import TelegramNotifier
from persistence.sqlite_store import PersistentPaperBroker


def fmt(p: float) -> str:
    return f"{p:,.2f}"


class TelegramEventAdapter:
    def __init__(self, persistent_broker: PersistentPaperBroker, notifier: TelegramNotifier) -> None:
        self.persistent_broker = persistent_broker
        self.notifier = notifier

    def process_candle(self, candle, candle_index: int) -> None:
        broker = self.persistent_broker.broker
        had_pending_before = broker.has_pending_entry
        state_before = broker.state
        trades_before = len(broker.get_closed_trades())

        self.persistent_broker.process_closed_candle(candle, candle_index)

        self._notify_signal(had_pending_before)
        self._notify_entry(state_before)
        self._notify_exit(trades_before)

    def _notify_signal(self, had_pending_before: bool) -> None:
        broker = self.persistent_broker.broker
        if broker.has_pending_entry and not had_pending_before:
            sig = broker._adapter._pending_signal
            self.notifier.send(
                f"\U0001F4E1 SIGNAL: BTC-USD {sig.direction} on candle closed at "
                f"{sig.signal_candle_timestamp}\n"
                f"initial_stop={fmt(sig.initial_stop)}\n"
                f"Will fill at the NEXT candle's OPEN (contract: N closed -> WAIT -> N+1 open)."
            )

    def _notify_entry(self, state_before: RuntimeState) -> None:
        broker = self.persistent_broker.broker
        if state_before == RuntimeState.FLAT and broker.state == RuntimeState.LONG:
            pos = broker.get_open_position()
            self.notifier.send(
                f"\u2705 ENTRY FILLED: BTC-USD LONG @ {fmt(pos.entry_price)}\n"
                f"qty={pos.quantity:.6f}  stop={fmt(pos.initial_stop)}\n"
                f"balance=${broker.balance:.2f}"
            )

    def _notify_exit(self, trades_before: int) -> None:
        broker = self.persistent_broker.broker
        trades_after = broker.get_closed_trades()
        if len(trades_after) > trades_before:
            t = trades_after[-1]
            result = "WIN \u2705" if t.net_pnl > 0 else "LOSS \u274C"
            self.notifier.send(
                f"\U0001F3C1 EXIT: BTC-USD @ {fmt(t.exit_price)}  reason={t.reason}\n"
                f"net_pnl={t.net_pnl:+.4f}  {result}\n"
                f"balance=${broker.balance:.2f}"
            )
