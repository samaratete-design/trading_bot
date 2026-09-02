from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class OrderType(Enum):
    """Direction of a trade order."""

    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Lifecycle status of an order as reported by a broker."""

    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"


@dataclass(frozen=True)
class Candle:
    """A single OHLCV price bar."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Signal:
    """
    A trade signal emitted by a strategy.

    Represents an intent to trade, not yet an executed order.
    """

    timestamp: datetime
    symbol: str
    order_type: OrderType
    entry_price: float
    stop_loss: float
    take_profit: float
    strategy_name: str


@dataclass(frozen=True)
class OrderResult:
    """
    Immutable record of the outcome of an order execution attempt.

    This represents the broker's ACK of an order being placed/filled.
    It intentionally does NOT carry exit_price or pnl — those belong
    to a ClosedTrade, which only exists once a position is closed.
    """

    order_id: str
    symbol: str
    order_type: OrderType
    filled_price: float
    size: float
    status: OrderStatus


@dataclass(frozen=True)
class Position:
    """
    An open position being tracked by the broker after an order fills.

    Created the moment an order is FILLED; lives until price hits
    stop_loss or take_profit (or is otherwise closed).
    """

    order_id: str
    symbol: str
    order_type: OrderType
    entry_price: float
    stop_loss: float
    take_profit: float
    size: float


@dataclass(frozen=True)
class ClosedTrade:
    """
    Immutable record of a fully closed trade, including realized PnL.
    """

    order_id: str
    symbol: str
    order_type: OrderType
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    exit_reason: str  # "STOP_LOSS" | "TAKE_PROFIT"
