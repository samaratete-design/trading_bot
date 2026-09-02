from __future__ import annotations

from dataclasses import dataclass
from core.models import Signal
from execution.broker_interface import OrderResult


@dataclass(frozen=True)
class SignalGeneratedEvent:
    """حدث يتم اطلاقه عند توليد إشارة تداول جديدة من الاستراتيجية."""
    signal: Signal


@dataclass(frozen=True)
class OrderExecutedEvent:
    """حدث يتم اطلاقه عند تنفيذ أمر تداول بنجاح عبر الوسيط."""
    order_result: OrderResult


@dataclass(frozen=True)
class RuntimeErrorEvent:
    """حدث يتم اطلاقه عند حدوث خطأ مفاجئ في أي مكون بالنظام."""
    error_message: str
    component: str
