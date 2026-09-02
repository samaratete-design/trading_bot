from dataclasses import dataclass
import pytest
from runtime.event_bus import EventBus


@dataclass(frozen=True)
class DummyEvent:
    message: str


def test_subscriber_receives_event() -> None:
    bus = EventBus()
    received = []

    bus.subscribe(DummyEvent, lambda e: received.append(e.message))
    bus.publish(DummyEvent(message="Test Message"))

    assert received == ["Test Message"]


def test_fault_isolation_in_subscribers() -> None:
    bus = EventBus()
    successful_runs = []

    def failing_subscriber(event: DummyEvent) -> None:
        raise ValueError("Crash!")

    def working_subscriber(event: DummyEvent) -> None:
        successful_runs.append(event.message)

    bus.subscribe(DummyEvent, failing_subscriber)
    bus.subscribe(DummyEvent, working_subscriber)

    # النشر يجب ألا يتأثر بفشل المشترك الأول
    bus.publish(DummyEvent(message="Resilient"))

    assert successful_runs == ["Resilient"]
