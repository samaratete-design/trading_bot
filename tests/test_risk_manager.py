from __future__ import annotations

from datetime import datetime
import pytest

from core.models import OrderType, Signal
from risk.risk_manager import RiskManager


@pytest.fixture
def sample_signal_for_risk() -> Signal:
    return Signal(
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        symbol="BTCUSDT",
        order_type=OrderType.BUY,
        entry_price=50_000.0,
        stop_loss=49_000.0,  # المسافة = 1000
        take_profit=52_000.0,
        strategy_name="test_strategy",
    )


def test_risk_manager_position_sizing(sample_signal_for_risk: Signal) -> None:
    # رأس المال = 10,000، نسبة المخاطرة = 1% (المبلغ المخاطر به = 100)
    # مسافة وقف الخسارة = 1,000 (من 50,000 إلى 49,000)
    # الحجم المتوقع = 100 / 1000 = 0.1
    risk_manager = RiskManager(risk_per_trade_pct=0.01)
    size = risk_manager.calculate_position_size(balance=10_000.0, signal=sample_signal_for_risk)

    assert size == 0.1


def test_risk_manager_zero_distance(sample_signal_for_risk: Signal) -> None:
    # لو كان سعر الدخول مساوياً لوقف الخسارة لتجنب القسمة على صفر
    zero_distance_signal = Signal(
        timestamp=sample_signal_for_risk.timestamp,
        symbol=sample_signal_for_risk.symbol,
        order_type=sample_signal_for_risk.order_type,
        entry_price=50_000.0,
        stop_loss=50_000.0,
        take_profit=52_000.0,
        strategy_name="test_strategy",
    )
    risk_manager = RiskManager()
    size = risk_manager.calculate_position_size(balance=10_000.0, signal=zero_distance_signal)

    assert size == 0.0
