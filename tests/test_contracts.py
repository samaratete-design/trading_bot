from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from core.models import OrderStatus, OrderType, Signal
from execution.broker_interface import OrderResult


@pytest.fixture
def sample_signal() -> Signal:
    return Signal(
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        symbol="BTCUSDT",
        order_type=OrderType.BUY,
        entry_price=50_000.0,
        stop_loss=49_000.0,
        take_profit=52_000.0,
        strategy_name="test_strategy",
    )


@pytest.fixture
def sample_order_result() -> OrderResult:
    return OrderResult(
        order_id="test-order-id",
        symbol="BTCUSDT",
        order_type=OrderType.BUY,
        filled_price=50_000.0,
        size=0.1,
        status=OrderStatus.FILLED,
    )


class TestSignalImmutability:
    def test_signal_cannot_modify_entry_price(self, sample_signal: Signal) -> None:
        with pytest.raises(FrozenInstanceError):
            sample_signal.entry_price = 60_000.0  # type: ignore[misc]


class TestOrderResultImmutability:
    def test_order_result_cannot_modify_filled_price(
        self, sample_order_result: OrderResult
    ) -> None:
        with pytest.raises(FrozenInstanceError):
            sample_order_result.filled_price = 51_000.0  # type: ignore[misc]


class TestOrderStatus:
    def test_order_status_has_expected_members(self) -> None:
        expected = {"FILLED", "REJECTED", "CANCELED", "PARTIALLY_FILLED"}
        actual = {member.name for member in OrderStatus}
        assert actual == expected
