from __future__ import annotations

from typing import Optional
from core.models import Candle, Signal, OrderType
from core.strategy_interface import BaseStrategy


class DummyAlwaysBuyStrategy(BaseStrategy):
    """
    استراتيجية تجريبية تولد إشارة شراء مع كل شمعة جديدة لأغراض الاختبار.
    """

    def calculate(self, candle: Candle) -> Optional[Signal]:
        return Signal(
            timestamp=candle.timestamp,
            symbol="BTCUSDT",
            order_type=OrderType.BUY,
            entry_price=candle.close,
            stop_loss=candle.close * 0.98,
            take_profit=candle.close * 1.02,
            strategy_name="DummyAlwaysBuy",
        )
