"""
execution/state_machine.py

Explicit runtime position state machine.

Per handoff contract item 9: the generic model supports FLAT/LONG/SHORT,
but BTC Trend v1 is long-only in its actual frozen behavior. This module
therefore keeps SHORT in the enum (so the model is generically correct and
self-documenting) but exposes NO method that can ever reach it -- there is
no open_short(). The only way into a position is open_long(). This is a
structural guard, not a convention: nothing in this file can create a
SHORT state.

Transitions are strict:
    FLAT -> LONG   via open_long()   -- rejected if already LONG
    LONG -> FLAT   via close()       -- rejected if already FLAT

Any other call raises InvalidStateTransitionError. There is no silent
no-op path -- a caller that tries to double-open or double-close a
position gets a loud error, not a swallowed duplicate.
"""

from __future__ import annotations

from enum import Enum


class RuntimeState(Enum):
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"  # part of the generic model; no code path reaches this in v1


class InvalidStateTransitionError(RuntimeError):
    """Raised on any attempted transition that would create a
    contradictory or duplicate state (e.g. LONG -> LONG, FLAT -> FLAT
    close, or any attempt to enter SHORT)."""


class PositionStateMachine:
    def __init__(self, state: RuntimeState = RuntimeState.FLAT) -> None:
        self._state = state

    @property
    def state(self) -> RuntimeState:
        return self._state

    def open_long(self) -> None:
        if self._state != RuntimeState.FLAT:
            raise InvalidStateTransitionError(
                f"cannot open_long() from {self._state.value} -- a position "
                "is already open. Duplicate entries are not allowed."
            )
        self._state = RuntimeState.LONG

    def close(self) -> None:
        if self._state != RuntimeState.LONG:
            raise InvalidStateTransitionError(
                f"cannot close() from {self._state.value} -- there is no "
                "open position to close."
            )
        self._state = RuntimeState.FLAT

    def force_set(self, state: RuntimeState) -> None:
        """Restart/recovery ONLY -- restores a previously-persisted state
        directly, bypassing transition guards. Never call this from normal
        trading flow."""
        self._state = state
