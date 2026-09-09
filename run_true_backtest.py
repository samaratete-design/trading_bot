from __future__ import annotations

import uuid

from strategies.btc_trend_v1 import (
    BTCTrendV1Strategy,
    PositionState,
)
from risk.risk_manager import RiskManager
from data.loader import CsvDataLoader
from core.models import (
    OrderType,
    OrderResult,
    OrderStatus,
    ClosedTrade,
)


print("=" * 60)
print("🚀 بدء المحاكاة التاريخية الحقيقية (True Backtest Engine)...")
print("=" * 60)


# ---------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------

strategy = BTCTrendV1Strategy()
risk_manager = RiskManager()

loader = CsvDataLoader("fixtures/btc_15m_full.csv")
candles = list(loader.load_candles())[-15000:]


initial_balance = 10_000.0
balance = initial_balance

print(f"[💰] الرصيد الابتدائي الافتراضي: ${initial_balance:,.2f}")
print(f"[📊] إجمالي الشموع المختبرة: {len(candles)}")
print("-" * 60)


# ---------------------------------------------------------------------
# Backtest state
# ---------------------------------------------------------------------

open_position = None
strategy_position = None

pending_signal = None

all_closed_trades = []
trades_executed = 0


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def calculate_pnl(entry_price: float, exit_price: float, size: float, order_type) -> float:
    if order_type == OrderType.BUY:
        return (exit_price - entry_price) * size

    return (entry_price - exit_price) * size


def make_strategy_position(entry_price: float, structure_stop: float) -> PositionState:
    """
    Create the mutable strategy-side position state.

    This is intentionally separate from core.models.Position.
    The strategy's evaluate_exit() mutates this object on every
    closed candle.
    """
    return PositionState(
        entry_price=entry_price,
        structure_stop=structure_stop,
        highest_high_since_entry=entry_price,
        trailing_active=False,
        current_trail_stop=None,
    )


# ---------------------------------------------------------------------
# Main historical replay
# ---------------------------------------------------------------------

for i, current_candle in enumerate(candles):

    # -------------------------------------------------------------
    # 1. Execute a pending entry at THIS candle's OPEN.
    #
    # The signal was generated from the previous closed candle.
    # Therefore there is no look-ahead here.
    # -------------------------------------------------------------

    just_entered = False

    if pending_signal is not None and open_position is None:

        signal = pending_signal

        execution_price = float(current_candle.open)

        # The strategy signal carries the structure stop generated
        # from the signal candle. The actual entry is the NEXT candle open.
        structure_stop = float(signal.initial_stop)

        # Build a temporary object compatible with RiskManager.
        class RiskSignal:
            pass

        risk_signal = RiskSignal()
        risk_signal.symbol = signal.symbol
        risk_signal.order_type = OrderType.BUY
        risk_signal.entry_price = execution_price
        risk_signal.stop_loss = structure_stop
        risk_signal.take_profit = None

        size = risk_manager.calculate_position_size(
            balance,
            risk_signal,
        )

        if size > 0:
            order_id = str(uuid.uuid4())

            order_result = OrderResult(
                order_id=order_id,
                symbol=signal.symbol,
                order_type=OrderType.BUY,
                filled_price=execution_price,
                size=size,
                status=OrderStatus.FILLED,
            )

            if order_result.status == OrderStatus.FILLED:

                open_position = {
                    "order_id": order_id,
                    "symbol": signal.symbol,
                    "order_type": OrderType.BUY,
                    "entry_price": execution_price,
                    "structure_stop": structure_stop,
                    "size": size,
                }

                strategy_position = make_strategy_position(
                    entry_price=execution_price,
                    structure_stop=structure_stop,
                )

                trades_executed += 1
                just_entered = True

        pending_signal = None


    # -------------------------------------------------------------
    # 2. Feed the CURRENT candle to the strategy.
    #
    # This is a fully closed candle from the backtest's perspective.
    # -------------------------------------------------------------

    strategy.on_closed_candle(current_candle)


    # -------------------------------------------------------------
    # 3. Manage an existing position using the STRATEGY exit engine.
    #
    # IMPORTANT:
    # - evaluate_exit() owns dynamic exit logic.
    # - No broker stop/take-profit engine is called.
    # - ExitInstruction.exit_price is used exactly as returned.
    # -------------------------------------------------------------

    exited_this_candle = False

    if open_position is not None and strategy_position is not None:

        exit_instruction = strategy.evaluate_exit(strategy_position)

        if exit_instruction is not None:

            exit_price = float(exit_instruction.exit_price)

            pnl = calculate_pnl(
                entry_price=open_position["entry_price"],
                exit_price=exit_price,
                size=open_position["size"],
                order_type=open_position["order_type"],
            )

            balance += pnl

            all_closed_trades.append(
                ClosedTrade(
                    order_id=open_position["order_id"],
                    symbol=open_position["symbol"],
                    order_type=open_position["order_type"],
                    entry_price=open_position["entry_price"],
                    exit_price=exit_price,
                    size=open_position["size"],
                    pnl=pnl,
                    exit_reason=exit_instruction.reason.value,
                )
            )

            open_position = None
            strategy_position = None

            exited_this_candle = True


    # -------------------------------------------------------------
    # 4. Evaluate a NEW entry ONLY if:
    #
    # - there is no open position
    # - there was no exit on this candle
    # - there is no already-pending entry
    #
    # This enforces "no same-candle re-entry".
    # -------------------------------------------------------------

    if (
        open_position is None
        and not exited_this_candle
        and pending_signal is None
    ):

        signal = strategy.evaluate_entry()

        if signal is not None:
            pending_signal = signal


# ---------------------------------------------------------------------
# End-of-data handling
# ---------------------------------------------------------------------
#
# An open position is NOT force-closed.
# It is reported as OPEN because forcing an exit at the last close
# would introduce an artificial strategy exit that does not exist.
# ---------------------------------------------------------------------

open_position_status = "OPEN" if open_position is not None else "NONE"


# ---------------------------------------------------------------------
# Final statistics
# ---------------------------------------------------------------------

final_balance = balance
net_profit = final_balance - initial_balance

roi = (
    (net_profit / initial_balance) * 100
    if initial_balance != 0
    else 0.0
)

winning_trades = [
    trade
    for trade in all_closed_trades
    if trade.pnl > 0
]

losing_trades = [
    trade
    for trade in all_closed_trades
    if trade.pnl <= 0
]

win_rate = (
    (len(winning_trades) / len(all_closed_trades)) * 100
    if all_closed_trades
    else 0.0
)


# ---------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------

print("=" * 60)
print("🏁 تقرير الأداء الحقيقي — Correct True Backtest")
print("=" * 60)

print(f"[📈] إجمالي الصفقات المنفذة: {trades_executed}")
print(f"[🔒] إجمالي الصفقات المغلقة: {len(all_closed_trades)}")

print(
    f"[🎯] الصفقات الرابحة / الخاسرة: "
    f"{len(winning_trades)} رابحة / {len(losing_trades)} خاسرة"
)

print(f"[🏆] نسبة النجاح (Win Rate): {win_rate:.2f}%")

print(f"[💰] الرصيد الابتدائي: ${initial_balance:,.2f}")
print(f"[💰] الرصيد النهائي: ${final_balance:,.2f}")

print(
    f"[📊] صافي الربح / الخسارة (Net PnL): "
    f"${net_profit:,.2f} ({roi:+.2f}%)"
)

print(f"[📌] حالة الصفقة عند نهاية البيانات: {open_position_status}")

print("=" * 60)
