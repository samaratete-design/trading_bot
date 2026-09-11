"""
strategies/btc_trend_v1.py

BTC-USD Trend Following v1 — BASELINE (not optimized)
========================================================
Symbol: BTC-USD only. No `if symbol == ...` branching — this file is
dedicated to BTC-USD exclusively, per the "fully independent strategies"
decision (EURUSD and XAUUSD will each get their own separate file/config).

Timeframe: 15m, single timeframe for everything (trend, strength, entry,
stop, trailing). No multi-timeframe in v1 — deliberate, to avoid a second
category of alignment bugs beyond what was already found in v6.5.2.

Direction: Long-only in v1 (explicit decision, not a limitation of the code
— see contract discussion for rationale).

IMPORTANT — INTEGRATION NOTE (read before wiring into the engine):
This strategy exposes TWO decision points, not one, because Chandelier
trailing exit and EMA-cross trend-break exit are DYNAMIC — they must be
re-evaluated on every closed candle for an OPEN position, not just decided
once at entry (unlike the old fixed SL/TP model):

    evaluate_entry(candle) -> Signal | None
        Called when there is NO open position. May return a new entry signal.

    evaluate_exit(candle, position_state) -> ExitInstruction | None
        Called when there IS an open position. May return an exit instruction
        (price + reason). Must be called BEFORE evaluate_entry on the same
        candle if a position is open, and a candle that closes a position
        must NOT also open a new one (per the "no lookahead / no same-candle
        re-entry" rule already agreed for the engine).

This does not match the old BaseStrategy.calculate(candle) single-method
interface described for the v6.5.2 system. You will need to either:
  (a) adapt core/strategy_interface.py to add an exit-evaluation hook, or
  (b) wrap this class in an adapter that matches whatever interface
      runtime/engine.py actually expects.
I have not seen the actual engine.py / strategy_interface.py content, so
I am not guessing at how to bolt this in — that reconciliation needs to
happen against the real files.

All decisions are made ONLY on fully closed candles. The caller is
responsible for only ever calling these methods with a closed candle
(no partial/forming candle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Deque
from collections import deque

from core.models import Candle, Signal, OrderType


class ExitReason(Enum):
    GAP = "gap"
    TRAILING_STOP = "trailing_stop"
    STRUCTURE_STOP = "structure_stop"
    TREND_BREAK = "trend_break"


@dataclass(frozen=True)
class ExitInstruction:
    exit_price: float
    reason: ExitReason


@dataclass
class PositionState:
    """
    Minimal state the caller (engine/broker) must track and pass back in on
    every candle while a position is open. This strategy is stateless across
    calls except for its own indicator history buffer — position lifecycle
    state belongs to the caller, not to the strategy instance, so multiple
    positions / restarts don't get tangled with strategy internals.
    """
    entry_price: float
    structure_stop: float          # the ORIGINAL structure stop, fixed at entry
    highest_high_since_entry: float
    trailing_active: bool = False  # becomes True once trailing stop > structure stop
    current_trail_stop: Optional[float] = None


# ---------------------------------------------------------------------------
# Indicator helpers — plain, dependency-free implementations.
# Termux/Android environments often lack talib; these are simple and exact
# enough for this contract. All operate on a list of closed candles, oldest
# first, and return None until there is enough warmup data.
# ---------------------------------------------------------------------------

def ema_series(values: List[float], period: int) -> List[Optional[float]]:
    """Standard EMA. Returns None for indices before the first `period`-length seed."""
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    multiplier = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = (values[i] - prev) * multiplier + prev
        out[i] = prev
    return out


def true_range(prev_close: float, high: float, low: float) -> float:
    return max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close),
    )


def atr_series(candles: List[Candle], period: int) -> List[Optional[float]]:
    """Wilder's ATR (standard for ATR-based stops)."""
    n = len(candles)
    out: List[Optional[float]] = [None] * n
    if n < period + 1:
        return out
    trs = [0.0] * n
    for i in range(1, n):
        trs[i] = true_range(candles[i - 1].close, candles[i].high, candles[i].low)
    seed = sum(trs[1:period + 1]) / period
    out[period] = seed
    prev = seed
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def adx_series(candles: List[Candle], period: int) -> List[Optional[float]]:
    """Wilder's ADX(14) — standard implementation."""
    n = len(candles)
    out: List[Optional[float]] = [None] * n
    if n < period * 2:
        return out

    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    trs = [0.0] * n

    for i in range(1, n):
        up_move = candles[i].high - candles[i - 1].high
        down_move = candles[i - 1].low - candles[i].low
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0
        trs[i] = true_range(candles[i - 1].close, candles[i].high, candles[i].low)

    smoothed_tr = sum(trs[1:period + 1])
    smoothed_plus_dm = sum(plus_dm[1:period + 1])
    smoothed_minus_dm = sum(minus_dm[1:period + 1])

    dx_values: List[Optional[float]] = [None] * n
    idx = period
    if smoothed_tr > 0:
        plus_di = 100 * smoothed_plus_dm / smoothed_tr
        minus_di = 100 * smoothed_minus_dm / smoothed_tr
        denom = plus_di + minus_di
        dx_values[idx] = 100 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0

    for i in range(period + 1, n):
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + trs[i]
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm[i]
        if smoothed_tr > 0:
            plus_di = 100 * smoothed_plus_dm / smoothed_tr
            minus_di = 100 * smoothed_minus_dm / smoothed_tr
            denom = plus_di + minus_di
            dx_values[i] = 100 * abs(plus_di - minus_di) / denom if denom > 0 else 0.0

    # ADX = smoothed average of DX, seeded after 2*period candles
    first_dx_idx = period
    adx_seed_start = first_dx_idx
    adx_seed_end = first_dx_idx + period
    valid_dx = [v for v in dx_values[adx_seed_start:adx_seed_end] if v is not None]
    if len(valid_dx) < period:
        return out
    adx = sum(valid_dx) / period
    out[adx_seed_end - 1] = adx
    for i in range(adx_seed_end, n):
        if dx_values[i] is None:
            continue
        adx = (adx * (period - 1) + dx_values[i]) / period
        out[i] = adx
    return out


def swing_low(candles: List[Candle], lookback: int, end_index: int) -> float:
    """Lowest low among the `lookback` closed candles ending at end_index (inclusive)."""
    start = max(0, end_index - lookback + 1)
    return min(c.low for c in candles[start:end_index + 1])


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class BTCTrendV1Strategy:
    SYMBOL = "BTC-USD"

    EMA_FAST_PERIOD = 50
    EMA_SLOW_PERIOD = 200
    EMA_ENTRY_PERIOD = 20
    ADX_PERIOD = 14
    ATR_PERIOD = 14
    ADX_THRESHOLD = 25.0
    SWING_LOOKBACK = 5
    STRUCTURE_STOP_ATR_MULT = 0.5
    CHANDELIER_ATR_MULT = 3.0

    # Warmup: EMA_SLOW_PERIOD is the binding constraint (200 > ADX's ~28, ATR's 14).
    MIN_WARMUP = EMA_SLOW_PERIOD

    def __init__(self) -> None:
        self._history: List[Candle] = []

    def on_closed_candle(self, candle: Candle) -> None:
        """Caller MUST call this exactly once per closed candle, in order,
        before calling evaluate_entry / evaluate_exit for that candle index."""
        self._history.append(candle)

    def _closes(self) -> List[float]:
        return [c.close for c in self._history]

    def _ready(self) -> bool:
        return len(self._history) > self.MIN_WARMUP

    def evaluate_exit(self, position: PositionState) -> Optional[ExitInstruction]:
        """
        Call this FIRST on every closed candle when a position is open,
        BEFORE evaluate_entry. Implements the exit priority order from the
        contract: gap -> trailing/structure stop hit -> trend break.
        """
        if not self._ready():
            return None

        candles = self._history
        i = len(candles) - 1
        current = candles[i]

        ema_slow = ema_series(self._closes(), self.EMA_SLOW_PERIOD)
        ema_fast = ema_series(self._closes(), self.EMA_FAST_PERIOD)
        atr = atr_series(candles, self.ATR_PERIOD)

        if atr[i] is None:
            return None  # not enough ATR warmup yet; should not happen once _ready()

        # Update highest high since entry (on every closed candle).
        position.highest_high_since_entry = max(
            position.highest_high_since_entry, current.high
        )

        candidate_trail = position.highest_high_since_entry - (
            atr[i] * self.CHANDELIER_ATR_MULT
        )

        # Trailing only ever activates once it's above the original structure
        # stop (otherwise it would loosen the stop, which the contract does
        # not allow — the stop only ever tightens).
        if candidate_trail > position.structure_stop:
            position.trailing_active = True
            if position.current_trail_stop is None:
                position.current_trail_stop = candidate_trail
            else:
                position.current_trail_stop = max(
                    position.current_trail_stop, candidate_trail
                )

        active_stop = (
            position.current_trail_stop
            if position.trailing_active and position.current_trail_stop is not None
            else position.structure_stop
        )

        # 1) Gap check
        if current.open <= active_stop:
            reason = (
                ExitReason.TRAILING_STOP
                if position.trailing_active
                else ExitReason.STRUCTURE_STOP
            )
            return ExitInstruction(exit_price=current.open, reason=reason)

        # 2) Stop hit intra-candle
        if current.low <= active_stop:
            reason = (
                ExitReason.TRAILING_STOP
                if position.trailing_active
                else ExitReason.STRUCTURE_STOP
            )
            return ExitInstruction(exit_price=active_stop, reason=reason)

        # 3) Trend break: EMA_FAST crosses below EMA_SLOW on this closed candle.
        if ema_fast[i] is not None and ema_slow[i] is not None:
            if ema_fast[i] < ema_slow[i]:
                return ExitInstruction(exit_price=current.close, reason=ExitReason.TREND_BREAK)

        return None

    def evaluate_entry(self) -> Optional[Signal]:
        """
        Call this only when there is NO open position (or after evaluate_exit
        just closed one on this same candle is handled by the caller as a
        SEPARATE candle — no same-candle re-entry, per the engine rule already
        agreed). Uses the two most recent closed candles: signal candle
        (i-1) and execution reference (i, the current closed candle acts as
        the signal here since we only decide on fully closed candles —
        the actual execution/fill happens on the NEXT candle's open, which
        is the caller's/broker's responsibility, not this method's).
        """
        if not self._ready():
            return None

        candles = self._history
        i = len(candles) - 1  # this is the signal candle (just closed)
        closes = self._closes()

        ema_slow = ema_series(closes, self.EMA_SLOW_PERIOD)
        ema_fast = ema_series(closes, self.EMA_FAST_PERIOD)
        ema_entry = ema_series(closes, self.EMA_ENTRY_PERIOD)
        adx = adx_series(candles, self.ADX_PERIOD)
        atr = atr_series(candles, self.ATR_PERIOD)

        if None in (ema_slow[i], ema_fast[i], ema_entry[i], adx[i], atr[i]):
            return None

        signal_candle = candles[i]

        # 1) Trend filter
        uptrend = ema_fast[i] > ema_slow[i] and signal_candle.close > ema_fast[i]
        if not uptrend:
            return None

        # 2) Trend strength filter
        if adx[i] < self.ADX_THRESHOLD:
            return None

        # 3) Pullback entry trigger on the signal candle:
        #    touched/pierced EMA20 (low <= ema20) then closed back above it,
        #    and the signal candle itself is bullish (close > open).
        touched_ema20 = signal_candle.low <= ema_entry[i]
        closed_above_ema20 = signal_candle.close > ema_entry[i]
        bullish_candle = signal_candle.close > signal_candle.open

        if not (touched_ema20 and closed_above_ema20 and bullish_candle):
            return None

        # 4) Structure stop
        s_low = swing_low(candles, self.SWING_LOOKBACK, i)
        structure_stop = s_low - (atr[i] * self.STRUCTURE_STOP_ATR_MULT)

        # Entry price: next candle's open. This method does not know the next
        # candle yet (it hasn't closed), so it returns the intended stop and
        # lets the caller (engine/broker) fill entry_price = next_open on the
        # following candle — matching "signal candle -> execution candle"
        # from the original contract. entry_price is therefore a PLACEHOLDER
        # here (set to signal_candle.close) that the caller MUST overwrite
        # with the actual next-candle open before using it for sizing/fill.
        return Signal(
            timestamp=signal_candle.timestamp,
            symbol=self.SYMBOL,
            order_type=OrderType.BUY,
            entry_price=signal_candle.close,  # placeholder — see docstring
            stop_loss=structure_stop,
            take_profit=None,
            strategy_name="btc_trend_v1",
        )
