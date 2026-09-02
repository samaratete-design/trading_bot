from __future__ import annotations

from core.models import Candle
from core.strategy_interface import BaseStrategy
from execution.broker_interface import BaseBroker
from risk.risk_manager import RiskManager
from runtime.event_bus import EventBus
from runtime.events import SignalGeneratedEvent, OrderExecutedEvent


class TradingEngine:
    """
    المحرك الرئيسي (Runtime Engine) الذي يربط الاستراتيجية ومدير المخاطر 
    بالوسيط عبر ناقل الأحداث (EventBus).
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        broker: BaseBroker,
        risk_manager: RiskManager,
        event_bus: EventBus,
    ) -> None:
        self.strategy = strategy
        self.broker = broker
        self.risk_manager = risk_manager
        self.event_bus = event_bus

        # الاشتراك في حدث توليد الإشارات لتنفيذها تلقائياً
        self.event_bus.subscribe(SignalGeneratedEvent, self._handle_signal)

    def process_candle(self, candle: Candle) -> None:
        """معالجة شمعة جديدة، تحديث الوسيط، وإصدار الحدث إذا توفرت إشارة."""
        # 1. تحديث الوسيط أولاً لفحص الصفقات المفتوحة وإغلاق ما يضرب SL/TP بناءً على الشمعة الحالية
        if hasattr(self.broker, "update_on_candle"):
            closed_trades = self.broker.update_on_candle(candle)
            # يمكنك هنا لاحقاً نشر أحداث للفقات المغلقة إذا رغبت

        # 2. توليد الإشارة من الاستراتيجية ومعالجتها
        signal = self.strategy.calculate(candle)
        if signal:
            event = SignalGeneratedEvent(signal=signal)
            self.event_bus.publish(event)

    def _handle_signal(self, event: SignalGeneratedEvent) -> None:
        """رد الفعل عند استقبال إشارة: حساب الحجم، إرسالها للوسيط، ونشر النتيجة."""
        current_balance = self.broker.get_account_balance()
        position_size = self.risk_manager.calculate_position_size(
            balance=current_balance, signal=event.signal
        )

        if position_size > 0:
            result = self.broker.execute_order(event.signal, size=position_size)
            order_event = OrderExecutedEvent(order_result=result)
            self.event_bus.publish(order_event)
