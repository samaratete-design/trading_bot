from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Dict, List

from core.models import (
    Candle,
    ClosedTrade,
    OrderResult,
    OrderStatus,
    OrderType,
    Position,
    Signal,
)


class BaseBroker(ABC):
    @abstractmethod
    def execute_order(self, signal: Signal, size: float) -> OrderResult:
        raise NotImplementedError

    @abstractmethod
    def get_account_balance(self) -> float:
        raise NotImplementedError


class PaperTradingBroker(BaseBroker):
    def __init__(
        self,
        initial_balance: float = 10_000.0,
        single_position_per_symbol: bool = False,
    ) -> None:
        self.balance = initial_balance
        self.single_position_per_symbol = single_position_per_symbol
        self._open_positions: Dict[str, Position] = {}

    def execute_order(self, signal: Signal, size: float) -> OrderResult:
        order_id = str(uuid.uuid4())

        if self.single_position_per_symbol and self._has_open_position(signal.symbol):
            return OrderResult(
                order_id=order_id,
                symbol=signal.symbol,
                order_type=signal.order_type,
                filled_price=signal.entry_price,
                size=size,
                status=OrderStatus.REJECTED,
            )

        result = OrderResult(
            order_id=order_id,
            symbol=signal.symbol,
            order_type=signal.order_type,
            filled_price=signal.entry_price,
            size=size,
            status=OrderStatus.FILLED,
        )

        self._open_positions[order_id] = Position(
            order_id=order_id,
            symbol=signal.symbol,
            order_type=signal.order_type,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            size=size,
        )

        return result

    def _has_open_position(self, symbol: str) -> bool:
        return any(p.symbol == symbol for p in self._open_positions.values())

    def get_account_balance(self) -> float:
        return self.balance

    def get_open_positions(self) -> List[Position]:
        return list(self._open_positions.values())

    def update_on_candle(self, candle: Candle) -> List[ClosedTrade]:
        closed_trades: List[ClosedTrade] = []

        for order_id, position in list(self._open_positions.items()):
            exit_price = None
            exit_reason = None

            if position.order_type == OrderType.BUY:
                if candle.low <= position.stop_loss:
                    exit_price = position.stop_loss
                    exit_reason = "STOP_LOSS"
                elif candle.high >= position.take_profit:
                    exit_price = position.take_profit
                    exit_reason = "TAKE_PROFIT"
            else:
                if candle.high >= position.stop_loss:
                    exit_price = position.stop_loss
                    exit_reason = "STOP_LOSS"
                elif candle.low <= position.take_profit:
                    exit_price = position.take_profit
                    exit_reason = "TAKE_PROFIT"

            if exit_price is None:
                continue

            pnl = self._calculate_pnl(position, exit_price)
            self.balance += pnl

            closed_trades.append(
                ClosedTrade(
                    order_id=position.order_id,
                    symbol=position.symbol,
                    order_type=position.order_type,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    size=position.size,
                    pnl=pnl,
                    exit_reason=exit_reason,
                )
            )

            del self._open_positions[order_id]

        return closed_trades

    @staticmethod
    def _calculate_pnl(position: Position, exit_price: float) -> float:
        if position.order_type == OrderType.BUY:
            return (exit_price - position.entry_price) * position.size
        return (position.entry_price - exit_price) * position.size
