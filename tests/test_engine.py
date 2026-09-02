from __future__ import annotations

from datetime import datetime
import pytest

from core.models import Candle, OrderStatus, OrderType
from execution.broker_interface import PaperTradingBroker
from risk.risk_manager import RiskManager
from runtime.event_bus import EventBus
from runtime.events import OrderExecutedEvent
from runtime.engine import TradingEngine
from strategies.dummy_strategy import DummyAlwaysBuyStrategy


def test_engine_full_pipeline() -> None:
    # 1. إعداد المكونات مع مدير المخاطر
    bus = EventBus()
    broker = PaperTradingBroker(initial_balance=10_000.0)
    risk_manager = RiskManager(risk_per_trade_pct=0.01)
    strategy = DummyAlwaysBuyStrategy()
    
    engine = TradingEngine(
        strategy=strategy, 
        broker=broker, 
        risk_manager=risk_manager, 
        event_bus=bus
    )

    # تتبع الأحداث الصادرة عن التنفيذ
    executed_results = []
    bus.subscribe(OrderExecutedEvent, lambda e: executed_results.append(e.order_result))

    # 2. إنشاء شمعة تجريبية (المسافة بين السعر ووقف الخسارة = 50,500 - 49,490 = 1010)
    candle = Candle(
        timestamp=datetime(2026, 1, 1, 12, 0, 0),
        open=50_000.0,
        high=51_000.0,
        low=49_000.0,
        close=50_500.0,
        volume=120.5,
    )

    # 3. معالجة الشمعة عبر المحرك
    engine.process_candle(candle)

    # 4. التحقق من صحة النتائج
    assert len(executed_results) == 1
    order_result = executed_results[0]
    
    assert order_result.symbol == "BTCUSDT"
    assert order_result.order_type == OrderType.BUY
    assert order_result.filled_price == 50_500.0
    assert order_result.status == OrderStatus.FILLED
    assert order_result.size > 0.0
    assert broker.get_account_balance() == 10_000.0
