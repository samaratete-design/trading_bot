"""
persistence/sqlite_store.py

Restart-durability layer around execution/paper_broker.py. Contains NO
strategy logic, NO sizing logic, NO exit logic -- it only persists and
replays what PaperBroker already computes.

WHY REPLAY, NOT JUST SNAPSHOT RESTORE:
The frozen strategy (strategies/btc_trend_v1.py) recomputes its indicators
from its FULL internal candle history on every call -- that is its actual,
unmodified behavior, and it's what the accepted Historical Runtime
Consistency Gate validated. A snapshot of balance/position alone cannot
reconstruct that internal indicator history. So this module persists every
closed candle ever fed to the runtime (append-only, indexed, duplicate-
proof), and restart recovery works by replaying that ENTIRE candle log
through a fresh PaperBroker/BTCTrendV1Adapter/BTCTrendV1Strategy from
scratch, in order, using ONLY the already-accepted on_closed_candle() path
-- the exact same call sequence used during the Consistency Gate run. This
guarantees the restored runtime is byte-identical to one that never
restarted, because it IS running the same deterministic replay, not an
approximation of it.

This means restore() never bypasses PositionStateMachine's transition
guards (no force_set here) -- the position state after restore was reached
through the same open_long()/close() calls a live run would have made, so
there is no path to a corrupted or contradictory state.

In addition to the candle log, a "read-model" snapshot (runtime_state,
open_position, pending_signal, closed_trades tables) is written after
every processed candle, purely so other components (a future status
query, Telegram) can read current state without replaying. Restore()
cross-checks this read-model against the freshly replayed broker state as
an integrity check -- if they ever disagree, that is a bug to surface, not
paper over.

Persisted `take_profit` is always NULL: BTC Trend v1 has no take-profit
concept (Chandelier trailing / structure-stop / trend-break exits only).
The column exists because the operational model is generically expected to
support it (per the handoff contract's generic order-state language), but
nothing in this runtime ever writes a value into it -- inventing one would
be inventing strategy behavior that doesn't exist.
"""

from __future__ import annotations

import sqlite3
from typing import List, Optional

from execution.paper_broker import DuplicateCandleError, PaperBroker
from execution.state_machine import RuntimeState
from core.models import Candle

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candles (
    idx INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    balance REAL NOT NULL,
    state TEXT NOT NULL,
    last_processed_index INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS open_position (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity REAL NOT NULL,
    initial_stop REAL NOT NULL,
    take_profit REAL,  -- always NULL for v1; see module docstring
    entry_time TEXT NOT NULL,
    entry_candle_index INTEGER NOT NULL,
    highest_high_since_entry REAL NOT NULL,
    trailing_active INTEGER NOT NULL,
    current_trail_stop REAL
);

CREATE TABLE IF NOT EXISTS pending_signal (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    initial_stop REAL NOT NULL,
    strategy_name TEXT NOT NULL,
    signal_candle_index INTEGER NOT NULL,
    signal_candle_timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS closed_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    net_pnl REAL NOT NULL,
    reason TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    exit_time TEXT NOT NULL,
    entry_candle_index INTEGER NOT NULL,
    exit_candle_index INTEGER NOT NULL
);
"""


class StateIntegrityError(RuntimeError):
    """Raised if the persisted read-model ever disagrees with the freshly
    replayed broker state on restore -- surfaced loudly, never silently
    reconciled."""


class PersistentPaperBroker:
    def __init__(self, db_path: str, starting_balance: float = 100.0, symbol: str = "BTC-USD") -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

        row = self._conn.execute("SELECT value FROM meta WHERE key='starting_balance'").fetchone()
        if row is None:
            self._conn.execute("INSERT INTO meta(key, value) VALUES ('starting_balance', ?)", (str(starting_balance),))
            self._conn.execute("INSERT INTO meta(key, value) VALUES ('symbol', ?)", (symbol,))
            self._conn.commit()
            self.starting_balance = starting_balance
            self.symbol = symbol
        else:
            self.starting_balance = float(row[0])
            self.symbol = self._conn.execute("SELECT value FROM meta WHERE key='symbol'").fetchone()[0]

        self.broker = PaperBroker(starting_balance=self.starting_balance, symbol=self.symbol)

    # -- live path: new closed candles --------------------------------
    def process_closed_candle(self, candle: Candle, candle_index: int) -> None:
        existing = self._conn.execute("SELECT 1 FROM candles WHERE idx = ?", (candle_index,)).fetchone()
        if existing is not None:
            raise DuplicateCandleError(
                f"candle_index {candle_index} already persisted -- refusing to "
                "reprocess (this check survives restart via the candles table)."
            )

        trades_before = len(self.broker.get_closed_trades())
        self.broker.on_closed_candle(candle, candle_index)
        trades_after = self.broker.get_closed_trades()

        self._conn.execute(
            "INSERT INTO candles (idx, timestamp, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
            (candle_index, str(candle.timestamp), candle.open, candle.high, candle.low, candle.close, candle.volume),
        )
        if len(trades_after) > trades_before:
            t = trades_after[-1]
            self._conn.execute(
                "INSERT INTO closed_trades (symbol, entry_price, exit_price, quantity, net_pnl, reason, "
                "entry_time, exit_time, entry_candle_index, exit_candle_index) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (t.symbol, t.entry_price, t.exit_price, t.quantity, t.net_pnl, t.reason,
                 str(t.entry_time), str(t.exit_time), t.entry_candle_index, t.exit_candle_index),
            )
        self._sync_read_model()
        self._conn.commit()

    def _sync_read_model(self) -> None:
        self._conn.execute("DELETE FROM runtime_state")
        self._conn.execute(
            "INSERT INTO runtime_state (id, balance, state, last_processed_index) VALUES (1, ?, ?, ?)",
            (self.broker.balance, self.broker.state.value, self.broker._last_processed_index),
        )

        self._conn.execute("DELETE FROM open_position")
        pos = self.broker.get_open_position()
        if pos is not None:
            sp = pos.strategy_position
            self._conn.execute(
                "INSERT INTO open_position (id, symbol, direction, entry_price, quantity, initial_stop, "
                "take_profit, entry_time, entry_candle_index, highest_high_since_entry, trailing_active, "
                "current_trail_stop) VALUES (1,?,?,?,?,?,NULL,?,?,?,?,?)",
                (pos.symbol, "LONG", pos.entry_price, pos.quantity, pos.initial_stop,
                 str(pos.entry_time), pos.entry_candle_index, sp.highest_high_since_entry,
                 int(sp.trailing_active), sp.current_trail_stop),
            )

        self._conn.execute("DELETE FROM pending_signal")
        if self.broker.has_pending_entry:
            sig = self.broker._adapter._pending_signal
            self._conn.execute(
                "INSERT INTO pending_signal (id, symbol, direction, initial_stop, strategy_name, "
                "signal_candle_index, signal_candle_timestamp) VALUES (1,?,?,?,?,?,?)",
                (sig.symbol, sig.direction, sig.initial_stop, sig.strategy_name,
                 sig.signal_candle_index, str(sig.signal_candle_timestamp)),
            )

    # -- restart recovery: replay, never snapshot-bypass ---------------
    @classmethod
    def restore(cls, db_path: str) -> "PersistentPaperBroker":
        instance = cls(db_path)  # opens existing DB, reads starting_balance/symbol from meta
        rows = instance._conn.execute(
            "SELECT idx, timestamp, open, high, low, close, volume FROM candles ORDER BY idx ASC"
        ).fetchall()

        for idx, ts, o, h, l, c, v in rows:
            candle = Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)
            # Deliberately calls the broker directly (not process_closed_candle)
            # -- these candles are already persisted; replaying them must not
            # re-insert or re-check against the candles table.
            instance.broker.on_closed_candle(candle, idx)

        instance._verify_against_read_model()
        return instance

    def _verify_against_read_model(self) -> None:
        row = self._conn.execute("SELECT balance, state, last_processed_index FROM runtime_state").fetchone()
        if row is None:
            return  # fresh DB, nothing to verify yet
        persisted_balance, persisted_state, persisted_last_idx = row
        if abs(persisted_balance - self.broker.balance) > 1e-9:
            raise StateIntegrityError(
                f"replayed balance {self.broker.balance} != persisted read-model "
                f"balance {persisted_balance} -- state divergence, not auto-fixed."
            )
        if persisted_state != self.broker.state.value:
            raise StateIntegrityError(
                f"replayed state {self.broker.state.value} != persisted read-model "
                f"state {persisted_state} -- state divergence, not auto-fixed."
            )
        if persisted_last_idx != self.broker._last_processed_index:
            raise StateIntegrityError(
                f"replayed last_processed_index {self.broker._last_processed_index} != "
                f"persisted {persisted_last_idx} -- state divergence, not auto-fixed."
            )

    # -- read-only accessors for tests / future status queries ---------
    def get_closed_trade_rows(self) -> List[tuple]:
        return self._conn.execute("SELECT * FROM closed_trades ORDER BY id ASC").fetchall()

    def get_open_position_row(self) -> Optional[tuple]:
        return self._conn.execute("SELECT * FROM open_position WHERE id=1").fetchone()

    def get_runtime_state_row(self) -> Optional[tuple]:
        return self._conn.execute("SELECT * FROM runtime_state WHERE id=1").fetchone()

    def get_last_candle(self) -> Optional[tuple]:
        """(idx, timestamp) of the most recently processed candle, or None
        if nothing has been processed yet. Used by the live runner to know
        where to resume polling from after a restart."""
        return self._conn.execute(
            "SELECT idx, timestamp FROM candles ORDER BY idx DESC LIMIT 1"
        ).fetchone()

    def close(self) -> None:
        self._conn.close()
