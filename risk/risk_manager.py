from __future__ import annotations

from core.models import Signal


class RiskManager:
    """
    مدير المخاطر المسؤول عن حساب حجم العقد (Position Size) 
    بناءً على رأس المال المتاح ونسبة المخاطرة المسموح بها لكل صفقة.
    """

    def __init__(self, risk_per_trade_pct: float = 0.01) -> None:
        self.risk_per_trade_pct = risk_per_trade_pct  # النسبة المخاطر بها (مثلاً 1%)

    def calculate_position_size(
        self, balance: float, signal: Signal
    ) -> float:
        """
        حساب حجم العقد بناءً على المسافة بين سعر الدخول ووقف الخسارة.
        """
        risk_amount = balance * self.risk_per_trade_pct
        price_risk_distance = abs(signal.entry_price - signal.stop_loss)

        if price_risk_distance == 0:
            return 0.0

        # حجم العقد = المبلغ المخاطر به / مسافة وقف الخسارة
        position_size = risk_amount / price_risk_distance
        return round(position_size, 4)
