from datetime import datetime
import pytest

from core.models import Candle, OrderType, Signal
from execution.broker_interface import PaperTradingBroker


def make_signal(
    order_type: OrderType = OrderType.BUY,
    entry_price: float = 100.0,
    stop_loss: float = 95.0,
    take_profit: float = 110.0,
    symbol: str = "BTCUSDT",
) -> Signal:
    return Signal(
        timestamp=datetime(2026, 1, 1),
        symbol=symbol,
        order_type=order_type,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        strategy_name="test_strategy",
    )


def make_candle(
    high: float,
    low: float,
    timestamp: datetime = datetime(2026, 1, 2),
    open_: float = 100.0,
    close: float = 100.0,
    volume: float = 1.0,
) -> Candle:
    return Candle(
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def test_execute_order_unchanged() -> None:
    broker = PaperTradingBroker()
    result = broker.execute_order(make_signal(), size=1.0)
    assert result.filled_price == 100.0
    assert result.size == 1.0


def test_buy_take_profit_realizes_positive_pnl() -> None:
    broker = PaperTradingBroker(initial_balance=10_000.0)
    broker.execute_order(
        make_signal(entry_price=100.0, stop_loss=95.0, take_profit=110.0),
        size=1.0,
    )

    closed = broker.update_on_candle(make_candle(high=111.0, low=105.0))

    assert len(closed) == 1
    assert closed[0].exit_reason == "TAKE_PROFIT"
    assert closed[0].pnl == pytest.approx(10.0)
    assert broker.get_account_balance() == pytest.approx(10_010.0)
    assert broker.get_open_positions() == []
