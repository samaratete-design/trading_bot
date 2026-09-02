from __future__ import annotations

from collections import deque
from typing import Deque, Optional

from core.models import Candle, OrderType, Signal
from core.strategy_interface import BaseStrategy


class MovingAverageCrossoverStrategy(BaseStrategy):
    def __init__(
        self,
        symbol: str,
        fast_period: int = 5,
        slow_period: int = 20,
        stop_loss_pct: float = 0.01,
        risk_reward_ratio: float = 2.0,
    ) -> None:
        if fast_period <= 0 or slow_period <= 0:
            raise ValueError("fast_period and slow_period must be positive")
        if fast_period >= slow_period:
            raise ValueError("fast_period must be smaller than slow_period")
        if stop_loss_pct <= 0:
            raise ValueError("stop_loss_pct must be positive")
        if risk_reward_ratio <= 0:
            raise ValueError("risk_reward_ratio must be positive")

        self.symbol = symbol
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.stop_loss_pct = stop_loss_pct
        self.risk_reward_ratio = risk_reward_ratio

        self._closes: Deque[float] = deque(maxlen=slow_period)
        self._previous_trend: Optional[str] = None

    def calculate(self, candle: Candle) -> Signal | None:
        self._closes.append(candle.close)

        if len(self._closes) < self.slow_period:
            return None

        fast_ma = self._average(self.fast_period)
        slow_ma = self._average(self.slow_period)

        current_trend = self._trend_from_averages(fast_ma, slow_ma)
        signal: Signal | None = None

        if current_trend is not None and self._previous_trend is not None:
            crossed_bullish = (
                current_trend == "BULL" and self._previous_trend == "BEAR"
            )
            crossed_bearish = (
                current_trend == "BEAR" and self._previous_trend == "BULL"
            )

            if crossed_bullish:
                signal = self._build_signal(candle, OrderType.BUY)
            elif crossed_bearish:
                signal = self._build_signal(candle, OrderType.SELL)

        if current_trend is not None:
            self._previous_trend = current_trend

        return signal

    def _build_signal(self, candle: Candle, order_type: OrderType) -> Signal:
        entry_price = candle.close

        if order_type == OrderType.BUY:
            stop_loss = entry_price * (1 - self.stop_loss_pct)
            take_profit = entry_price * (
                1 + self.stop_loss_pct * self.risk_reward_ratio
            )
        else:
            stop_loss = entry_price * (1 + self.stop_loss_pct)
            take_profit = entry_price * (
                1 - self.stop_loss_pct * self.risk_reward_ratio
            )

        return Signal(
            timestamp=candle.timestamp,
            symbol=self.symbol,
            order_type=order_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            strategy_name="ma_crossover",
        )

    def _average(self, period: int) -> float:
        window = list(self._closes)[-period:]
        return sum(window) / len(window)

    @staticmethod
    def _trend_from_averages(fast_ma: float, slow_ma: float) -> Optional[str]:
        if fast_ma > slow_ma:
            return "BULL"
        if fast_ma < slow_ma:
            return "BEAR"
        return None
