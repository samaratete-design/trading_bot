from __future__ import annotations

from typing import Any, Callable, Dict, List, Type
import logging

class EventBus:
    """
    ناقل أحداث خفيف ومعزول يربط بين مكونات النظام المختلفة 
    بحيث يمنع أي استثناء في المشتركين (Subscribers) من إيقاف دورة الحياة الرئيسية.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[Type[Any], List[Callable[[Any], None]]] = {}
        self._logger = logging.getLogger("EventBus")

    def subscribe(self, event_type: Type[Any], callback: Callable[[Any], None]) -> None:
        """تسجيل دالة استجابة (Subscriber) لنوع محدد من الأحداث."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(callback)

    def publish(self, event: Any) -> None:
        """نشر حدث لجميع المشتركين مع ضمان عزل الأخطاء."""
        event_type = type(event)
        if event_type in self._subscribers:
            for callback in self._subscribers[event_type]:
                try:
                    callback(event)
                except Exception as e:
                    self._logger.error(
                        f"Error in subscriber handling {event_type.__name__}: {e}"
                    )
