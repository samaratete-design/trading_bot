"""
execution/strategy_adapter.py

Adapter layer between the FROZEN strategies/btc_trend_v1.py and the runtime.
strategies/btc_trend_v1.py is NOT imported for its side effects, NOT
modified, and NOT reimplemented here. This file only translates types and
sequences calls.

Strategy contract (unchanged):
    - BTCTrendV1Strategy.evaluate_entry() returns a LOCAL Signal(symbol,
      direction, entry_price, initial_stop, strategy) where entry_price is
      a documented PLACEHOLDER (== signal_candle.close). It must NEVER be
      used as an actual fill price.
    - BTCTrendV1Strategy.evaluate_exit(position) returns a LOCAL
      ExitInstruction(exit_price, reason: ExitReason).

This adapter enforces the closed-candle execution contract:

    Closed Candle N   -> strategy.evaluate_entry() may return a signal
          |
        WAIT
          |
    Candle N+1 OPENS  -> fill happens at N+1.open, and ONLY N+1.open

This is enforced STRUCTURALLY, not just by convention: resolve_pending_entry()
takes a bare (price, timestamp) pair, never a full Candle object. A caller
cannot leak N+1's close/high/low/indicators into the fill decision even by
accident, because the method signature has no way to receive them.

This holds identically for:
  - an offline/historical runtime, which may already possess the complete
    N+1 candle (e.g. iterating a CSV) but MUST extract only
    next_candle.open before calling resolve_pending_entry -- it is free to
    hold onto the rest of that candle for its own bookkeeping, just not
    pass it into this call.
  - a live paper runtime, which should call resolve_pending_entry the
    moment N+1's open print is observed in real time -- it does NOT need
    to wait for N+1 to close.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from strategies.btc_trend_v1 import (
    BTCTrendV1Strategy,
    Candle as StrategyCandle,
    PositionState,
)


@dataclass(frozen=True)
class AdapterSignal:
    """
    Runtime-facing translation of the strategy's local Signal.

    Deliberately has NO entry_price field. The strategy's own entry_price
    is a placeholder (signal_candle.close) and must never reach the
    runtime -- carrying it here would make it too easy for a future caller
    to use it by mistake. The real fill price only exists once
    resolve_pending_entry() is called with the next candle's open.
    """
    symbol: str
    direction: str  # "BUY" -- v1 is long-only, passed through as-is
    initial_stop: float
    strategy_name: str
    signal_candle_index: int
    signal_candle_timestamp: object


@dataclass(frozen=True)
class AdapterExitInstruction:
    """Runtime-facing translation of the strategy's local ExitInstruction."""
    exit_price: float
    reason: str  # ExitReason.value: "gap" | "trailing_stop" | "structure_stop" | "trend_break"


@dataclass(frozen=True)
class ResolvedEntry:
    """
    The result of resolving a pending signal at N+1's open. This -- and
    only this -- is what the rest of the runtime is allowed to treat as an
    actual fill price.
    """
    symbol: str
    direction: str
    fill_price: float  # == the next_open passed in, unmodified
    initial_stop: float
    strategy_name: str
    fill_timestamp: object
    signal_candle_index: int


class LookaheadError(RuntimeError):
    """
    Raised if the runtime attempts to resolve a pending signal that
    doesn't exist, resolve one twice, or request a new signal while one is
    still pending -- all of which indicate a broken execution sequence
    upstream of this adapter. Deliberately loud: a forgotten or double-
    resolved signal must never fail silently.
    """


class BTCTrendV1Adapter:
    def __init__(self) -> None:
        self._strategy = BTCTrendV1Strategy()
        self._pending_signal: Optional[AdapterSignal] = None
        self._candle_index = -1

    # -- candle intake ---------------------------------------------------
    def on_closed_candle(self, candle: StrategyCandle) -> None:
        """Must be called exactly once per closed candle, in order, before
        check_exit / check_entry for that candle."""
        self._strategy.on_closed_candle(candle)
        self._candle_index += 1

    # -- exit path (delegates entirely to strategy.evaluate_exit) --------
    def check_exit(self, position: PositionState) -> Optional[AdapterExitInstruction]:
        instr = self._strategy.evaluate_exit(position)
        if instr is None:
            return None
        return AdapterExitInstruction(exit_price=instr.exit_price, reason=instr.reason.value)

    # -- entry path: step 1, decided on candle N (just closed) -----------
    def check_entry(self, candle_timestamp: object) -> Optional[AdapterSignal]:
        """
        Call only when flat. If the strategy fires on this (just-closed)
        candle, the signal is stored internally AND returned so the
        runtime can log/observe it -- but it carries no fill price. The
        runtime must WAIT for the next closed candle's open before any
        fill can happen.
        """
        if self._pending_signal is not None:
            raise LookaheadError(
                "check_entry called while a signal from a previous candle "
                "is still pending resolution -- resolve or explicitly "
                "discard the pending signal before requesting a new one."
            )
        raw_signal = self._strategy.evaluate_entry()
        if raw_signal is None:
            return None
        adapter_signal = AdapterSignal(
            symbol=raw_signal.symbol,
            direction=raw_signal.order_type.value,
            initial_stop=raw_signal.stop_loss,
            strategy_name=raw_signal.strategy_name,
            signal_candle_index=self._candle_index,
            signal_candle_timestamp=candle_timestamp,
        )
        self._pending_signal = adapter_signal
        return adapter_signal

    # -- entry path: step 2, resolved ONLY at candle N+1's OPEN ----------
    def resolve_pending_entry(self, next_open: float, next_timestamp: object) -> ResolvedEntry:
        """
        Fills the pending signal at next_open. Takes a bare price +
        timestamp, never a Candle object -- deliberate: makes it
        structurally impossible for close/high/low/indicator values from
        candle N+1 to reach the fill decision, regardless of what the
        caller already holds from its data source.
        """
        if self._pending_signal is None:
            raise LookaheadError(
                "resolve_pending_entry called with no pending signal -- "
                "check_entry must return a signal before this can be called."
            )
        pending = self._pending_signal
        self._pending_signal = None
        return ResolvedEntry(
            symbol=pending.symbol,
            direction=pending.direction,
            fill_price=next_open,
            initial_stop=pending.initial_stop,
            strategy_name=pending.strategy_name,
            fill_timestamp=next_timestamp,
            signal_candle_index=pending.signal_candle_index,
        )

    def discard_pending_entry(self) -> None:
        """
        Explicit escape hatch if the runtime decides not to act on a
        pending signal (e.g. a risk gate rejects it before N+1 opens).
        Must be called explicitly -- there is no silent auto-discard, so a
        forgotten pending signal always surfaces as a LookaheadError
        rather than a silently dropped trade.
        """
        self._pending_signal = None

    @property
    def has_pending_entry(self) -> bool:
        return self._pending_signal is not None
