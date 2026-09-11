"""
execution/paper_broker.py

Operates the FROZEN BTC Trend v1 strategy deterministically in PAPER mode.
This file contains NO strategy logic of its own:
    - entry/exit conditions live entirely in strategies/btc_trend_v1.py
    - exit decisions are 100% delegated to BTCTrendV1Adapter.check_exit(),
      which calls strategy.evaluate_exit() -- nothing here re-decides or
      overrides an exit
    - sizing/fee/slippage math lives entirely in risk/position_sizer.py,
      reproducing the exact walk_forward_full_dataset.py semantics

PAPER-ONLY: there is no method on this class, anywhere, that submits an
order to a real exchange. MODE is a fixed class constant. There is no
LiveBroker here and no code path that could accidentally call one -- if
live trading is ever wanted, it is a deliberate future class, not a flag
on this one.

Sequencing per closed candle (mirrors walk_forward_full_dataset.py exactly,
just spread across explicit method calls instead of a single loop body):

    1. adapter.on_closed_candle(candle)          # strategy sees this candle
    2. if a signal is pending from the PREVIOUS candle:
           resolve it using ONLY this candle's .open -> maybe open LONG
    3. if position is LONG (whether just opened in step 2, or already open):
           delegate to adapter.check_exit() -> maybe close back to FLAT
           (a candle that closes a position does NOT also open a new one
           on the same candle -- matches the strategy's own contract)
    4. if position is FLAT and nothing happened in steps 2/3:
           delegate to adapter.check_entry() -> may flag a new pending
           signal, which will only be resolved on the NEXT candle's open
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from core.models import OrderResult, OrderStatus, OrderType
from execution.state_machine import (
    InvalidStateTransitionError,
    PositionStateMachine,
    RuntimeState,
)
from execution.strategy_adapter import BTCTrendV1Adapter, ResolvedEntry
from risk.position_sizer import NoTradeError, size_entry, size_exit
from strategies.btc_trend_v1 import PositionState


class DuplicateCandleError(RuntimeError):
    """Raised if on_closed_candle() is called with a candle_index that has
    already been processed. Must survive restart -- see get_state_snapshot
    / restore_from_snapshot."""


@dataclass
class OpenPositionRecord:
    symbol: str
    entry_price: float
    quantity: float
    initial_stop: float
    entry_time: object
    entry_candle_index: int
    strategy_position: PositionState  # passed back into strategy.evaluate_exit


@dataclass(frozen=True)
class ClosedTradeRecord:
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    net_pnl: float
    reason: str
    entry_time: object
    exit_time: object
    entry_candle_index: int
    exit_candle_index: int


class PaperBroker:
    MODE = "PAPER"  # fixed. Nothing in this class reads or branches on
                     # anything else; there is no way to flip this to LIVE.

    def __init__(self, starting_balance: float = 100.0, symbol: str = "BTC-USD") -> None:
        self.symbol = symbol
        self.balance = starting_balance
        self._state_machine = PositionStateMachine()
        self._adapter = BTCTrendV1Adapter()
        self._open_position: Optional[OpenPositionRecord] = None
        self._closed_trades: List[ClosedTradeRecord] = []
        self._order_log: List[OrderResult] = []
        self._last_processed_index: int = -1

    # -- read-only views ---------------------------------------------
    @property
    def state(self) -> RuntimeState:
        return self._state_machine.state

    def get_open_position(self) -> Optional[OpenPositionRecord]:
        return self._open_position

    def get_closed_trades(self) -> List[ClosedTradeRecord]:
        return list(self._closed_trades)

    def get_order_log(self) -> List[OrderResult]:
        return list(self._order_log)

    @property
    def has_pending_entry(self) -> bool:
        return self._adapter.has_pending_entry

    # -- main driver ----------------------------------------------------
    def on_closed_candle(self, candle, candle_index: int) -> None:
        if candle_index <= self._last_processed_index:
            raise DuplicateCandleError(
                f"candle_index {candle_index} already processed "
                f"(last_processed_index={self._last_processed_index}) -- "
                "a closed candle must be processed at most once."
            )
        self._last_processed_index = candle_index

        # Step 1: strategy always sees the candle, regardless of position state.
        self._adapter.on_closed_candle(candle)

        # Step 2: resolve any pending entry using ONLY this candle's open.
        if self._adapter.has_pending_entry:
            resolved = self._adapter.resolve_pending_entry(candle.open, candle.timestamp)
            self._try_open_position(resolved, candle_index)

        # Step 3: if in a position, exit is 100% delegated to the adapter.
        if self._state_machine.state == RuntimeState.LONG:
            exit_instr = self._adapter.check_exit(self._open_position.strategy_position)
            if exit_instr is not None:
                self._close_position(exit_instr, candle, candle_index)
                return  # no same-candle re-entry

        # Step 4: only ask for a new signal when flat.
        if self._state_machine.state == RuntimeState.FLAT:
            self._adapter.check_entry(candle.timestamp)

    # -- internals --------------------------------------------------------
    def _try_open_position(self, resolved: ResolvedEntry, candle_index: int) -> None:
        if resolved.direction != "BUY":
            # v1 is long-only by contract; this must never silently create
            # a SHORT position. PositionStateMachine has no open_short() at
            # all, so this guard is a defense-in-depth belt, not the only lock.
            raise InvalidStateTransitionError(
                f"unsupported direction {resolved.direction!r} -- BTC Trend "
                "v1 is long-only; the runtime must not invent short signals."
            )
        try:
            sized = size_entry(self.balance, resolved.fill_price, resolved.initial_stop)
        except NoTradeError:
            self._order_log.append(OrderResult(
                order_id=f"rej-{candle_index}",
                symbol=self.symbol,
                order_type=OrderType.BUY,
                filled_price=resolved.fill_price,
                size=0.0,
                status=OrderStatus.REJECTED,
            ))
            return  # stays FLAT; no position created

        self._state_machine.open_long()
        self.balance -= sized.entry_fee

        strategy_position = PositionState(
            entry_price=sized.entry_price,
            structure_stop=resolved.initial_stop,
            highest_high_since_entry=resolved.fill_price,
        )
        self._open_position = OpenPositionRecord(
            symbol=resolved.symbol,
            entry_price=sized.entry_price,
            quantity=sized.quantity,
            initial_stop=resolved.initial_stop,
            entry_time=resolved.fill_timestamp,
            entry_candle_index=candle_index,
            strategy_position=strategy_position,
        )
        self._order_log.append(OrderResult(
            order_id=f"fill-{candle_index}",
            symbol=self.symbol,
            order_type=OrderType.BUY,
            filled_price=sized.entry_price,
            size=sized.quantity,
            status=OrderStatus.FILLED,
        ))

    def _close_position(self, exit_instr, candle, candle_index: int) -> None:
        pos = self._open_position
        sized = size_exit(exit_instr.exit_price, pos.entry_price, pos.quantity)
        self.balance += sized.net_pnl
        self._closed_trades.append(ClosedTradeRecord(
            symbol=pos.symbol,
            entry_price=pos.entry_price,
            exit_price=sized.exit_price,
            quantity=pos.quantity,
            net_pnl=sized.net_pnl,
            reason=exit_instr.reason,
            entry_time=pos.entry_time,
            exit_time=candle.timestamp,
            entry_candle_index=pos.entry_candle_index,
            exit_candle_index=candle_index,
        ))
        self._state_machine.close()
        self._open_position = None

    # -- restart safety (bookkeeping only -- see limitations below) -------
    def get_state_snapshot(self) -> dict:
        """
        Captures broker-level bookkeeping state: balance, position state,
        open position, pending signal metadata, last processed candle
        index, and closed trade count.

        LIMITATION (explicitly not solved here): this does NOT capture the
        frozen strategy's internal indicator warmup history
        (BTCTrendV1Strategy._history). A restored adapter starts with an
        empty history and needs MIN_WARMUP closed candles replayed through
        on_closed_candle() before evaluate_entry()/evaluate_exit() are
        ready again. Full replay-based persistence is deferred to the
        dedicated Persistence phase (SQLite, per the handoff's item 15) --
        this snapshot only prevents duplicate candle processing and
        preserves position/balance continuity across a restart.
        """
        pending = None
        if self._adapter.has_pending_entry:
            sig = self._adapter._pending_signal
            pending = {
                "symbol": sig.symbol,
                "direction": sig.direction,
                "initial_stop": sig.initial_stop,
                "strategy_name": sig.strategy_name,
                "signal_candle_index": sig.signal_candle_index,
                "signal_candle_timestamp": sig.signal_candle_timestamp,
            }
        open_pos = None
        if self._open_position is not None:
            p = self._open_position
            open_pos = {
                "symbol": p.symbol,
                "entry_price": p.entry_price,
                "quantity": p.quantity,
                "initial_stop": p.initial_stop,
                "entry_time": p.entry_time,
                "entry_candle_index": p.entry_candle_index,
                "highest_high_since_entry": p.strategy_position.highest_high_since_entry,
                "trailing_active": p.strategy_position.trailing_active,
                "current_trail_stop": p.strategy_position.current_trail_stop,
            }
        return {
            "mode": self.MODE,
            "symbol": self.symbol,
            "balance": self.balance,
            "state": self._state_machine.state.value,
            "last_processed_index": self._last_processed_index,
            "open_position": open_pos,
            "pending_signal": pending,
            "closed_trade_count": len(self._closed_trades),
        }

    @classmethod
    def restore_from_snapshot(cls, snapshot: dict) -> "PaperBroker":
        """
        Rehydrates a broker from get_state_snapshot() output. The
        strategy's own indicator history is NOT restored (see the
        limitation documented on get_state_snapshot) -- the caller is
        responsible for replaying at least MIN_WARMUP prior closed candles
        through on_closed_candle() before trusting new entry signals, if
        the strategy needs to keep evaluating.
        """
        broker = cls(starting_balance=snapshot["balance"], symbol=snapshot["symbol"])
        broker._last_processed_index = snapshot["last_processed_index"]
        broker._state_machine.force_set(RuntimeState(snapshot["state"]))

        if snapshot["open_position"] is not None:
            op = snapshot["open_position"]
            strategy_position = PositionState(
                entry_price=op["entry_price"],
                structure_stop=op["initial_stop"],
                highest_high_since_entry=op["highest_high_since_entry"],
                trailing_active=op["trailing_active"],
                current_trail_stop=op["current_trail_stop"],
            )
            broker._open_position = OpenPositionRecord(
                symbol=op["symbol"],
                entry_price=op["entry_price"],
                quantity=op["quantity"],
                initial_stop=op["initial_stop"],
                entry_time=op["entry_time"],
                entry_candle_index=op["entry_candle_index"],
                strategy_position=strategy_position,
            )

        if snapshot["pending_signal"] is not None:
            ps = snapshot["pending_signal"]
            from execution.strategy_adapter import AdapterSignal
            broker._adapter._pending_signal = AdapterSignal(
                symbol=ps["symbol"],
                direction=ps["direction"],
                initial_stop=ps["initial_stop"],
                strategy_name=ps["strategy_name"],
                signal_candle_index=ps["signal_candle_index"],
                signal_candle_timestamp=ps["signal_candle_timestamp"],
            )

        return broker
