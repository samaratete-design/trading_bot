"""
tests/test_telegram_event_adapter.py

Fully offline tests for live/telegram_event_adapter.py. No Binance call,
no real Telegram token, no network -- TelegramNotifier.send is mocked to
capture messages instead of sending them, and entry/exit signals are
produced via the same replay-safe class-level strategy patch pattern used
in tests/test_persistence.py (deterministic, keyed off candle timestamp,
not an instance-level stub -- see that file's module docstring for why).

Run: python3 -m unittest tests.test_telegram_event_adapter -v
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from execution.state_machine import RuntimeState
from live.telegram_event_adapter import TelegramEventAdapter
from live.telegram_notifier import TelegramNotifier
from persistence.sqlite_store import PersistentPaperBroker
from strategies.btc_trend_v1 import BTCTrendV1Strategy, Candle, ExitInstruction, ExitReason
from core.models import OrderType, Signal as StrategySignal

SIGNAL_TS = "SIGNAL-TRIGGER"
FILL_TS = "2026-03-01T00:15:00Z"
EXIT_TS = "2026-03-01T00:30:00Z"
QUIET_TS = "2026-03-01T00:45:00Z"


def _candle(t, o=41500.0, h=41600.0, l=41400.0, c=41550.0, v=1.0):
    return Candle(timestamp=t, open=o, high=h, low=l, close=c, volume=v)


def _patched_evaluate_entry(self):
    if self._history and str(self._history[-1].timestamp) == SIGNAL_TS:
        candle = self._history[-1]
        return StrategySignal(
            timestamp=candle.timestamp,
            symbol="BTC-USD",
            order_type=OrderType.BUY,
            entry_price=candle.close,
            stop_loss=41000.0,
            take_profit=None,
            strategy_name="btc_trend_v1",
        )
    return None


def _patched_evaluate_exit(self, position):
    if self._history and str(self._history[-1].timestamp) == EXIT_TS:
        return ExitInstruction(exit_price=41800.0, reason=ExitReason.TRAILING_STOP)
    return None


def replay_safe_patch():
    return patch.multiple(
        BTCTrendV1Strategy,
        evaluate_entry=_patched_evaluate_entry,
        evaluate_exit=_patched_evaluate_exit,
    )


class FakeNotifier(TelegramNotifier):
    """Captures messages instead of hitting the network -- this is the
    'mocked Telegram' side of the offline test."""
    def __init__(self):
        self.sent = []
        self.enabled = True  # bypass the env-var check entirely

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


class TempDB(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.remove(self.db_path)
        self.pb = PersistentPaperBroker(self.db_path, starting_balance=100.0, symbol="BTC-USD")
        self.notifier = FakeNotifier()
        self.adapter = TelegramEventAdapter(self.pb, self.notifier)

    def tearDown(self):
        self.pb.close()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)


class TestQuietCandleSendsNothing(TempDB):
    def test_no_event_no_message(self):
        self.adapter.process_candle(_candle(QUIET_TS), candle_index=0)
        self.assertEqual(self.notifier.sent, [])


class TestSignalEvent(TempDB):
    def test_signal_candle_sends_signal_message_only(self):
        with replay_safe_patch():
            self.adapter.process_candle(_candle(SIGNAL_TS), candle_index=0)
        self.assertEqual(len(self.notifier.sent), 1)
        self.assertIn("SIGNAL", self.notifier.sent[0])
        self.assertIn("BUY", self.notifier.sent[0])
        self.assertTrue(self.pb.broker.has_pending_entry)


class TestEntryFilledEvent(TempDB):
    def test_fill_candle_sends_entry_filled_message(self):
        with replay_safe_patch():
            self.adapter.process_candle(_candle(SIGNAL_TS), candle_index=0)
            self.notifier.sent.clear()
            self.adapter.process_candle(_candle(FILL_TS, o=41520.0), candle_index=1)

        self.assertEqual(len(self.notifier.sent), 1)
        self.assertIn("ENTRY FILLED", self.notifier.sent[0])
        self.assertIn("LONG", self.notifier.sent[0])
        self.assertEqual(self.pb.broker.state, RuntimeState.LONG)


class TestExitEventWinLoss(TempDB):
    def test_exit_candle_sends_exit_message_with_win(self):
        with replay_safe_patch():
            self.adapter.process_candle(_candle(SIGNAL_TS), candle_index=0)
            self.adapter.process_candle(_candle(FILL_TS, o=41520.0), candle_index=1)
            self.notifier.sent.clear()
            self.adapter.process_candle(_candle(EXIT_TS, o=41750.0, h=41850.0, l=41700.0, c=41820.0), candle_index=2)

        self.assertEqual(len(self.notifier.sent), 1)
        msg = self.notifier.sent[0]
        self.assertIn("EXIT", msg)
        self.assertIn("trailing_stop", msg)
        self.assertIn("WIN", msg)  # exit_price 41800 > entry ~41528 -> profitable
        self.assertEqual(self.pb.broker.state, RuntimeState.FLAT)


class TestNoMessageOnCandleWithoutStateChange(TempDB):
    def test_candle_while_long_with_no_exit_sends_nothing(self):
        with replay_safe_patch():
            self.adapter.process_candle(_candle(SIGNAL_TS), candle_index=0)
            self.adapter.process_candle(_candle(FILL_TS, o=41520.0), candle_index=1)
            self.notifier.sent.clear()
            # a candle that triggers neither entry nor exit while LONG
            self.adapter.process_candle(_candle(QUIET_TS, o=41600.0), candle_index=2)
        self.assertEqual(self.notifier.sent, [])
        self.assertEqual(self.pb.broker.state, RuntimeState.LONG)


class TestRestartDoesNotDuplicateNotificationTrigger(TempDB):
    def test_restore_then_new_candle_fires_exactly_one_exit_message(self):
        with replay_safe_patch():
            self.adapter.process_candle(_candle(SIGNAL_TS), candle_index=0)
            self.adapter.process_candle(_candle(FILL_TS, o=41520.0), candle_index=1)
            self.pb.close()

            restored_pb = PersistentPaperBroker.restore(self.db_path)
            restored_notifier = FakeNotifier()
            restored_adapter = TelegramEventAdapter(restored_pb, restored_notifier)

            restored_adapter.process_candle(
                _candle(EXIT_TS, o=41750.0, h=41850.0, l=41700.0, c=41820.0), candle_index=2
            )
            self.assertEqual(len(restored_notifier.sent), 1)
            self.assertIn("EXIT", restored_notifier.sent[0])
            self.assertEqual(len(restored_pb.get_closed_trade_rows()), 1)
            restored_pb.close()

        # prevent tearDown from double-closing the already-closed original pb
        self.pb.close = lambda: None


if __name__ == "__main__":
    unittest.main()
