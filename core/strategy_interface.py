from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
from core.models import Candle, Signal


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    Defines the contract for analyzing a candle and emitting a signal.
    """

    @abstractmethod
    def calculate(self, candle: Candle) -> Optional[Signal]:
        """Analyze the current candle and return a trade signal or None."""
        raise NotImplementedError
