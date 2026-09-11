"""
risk/position_sizer.py

Reproduces the EXACT sizing/fee/slippage semantics of the authoritative
baseline (walk_forward_full_dataset.py::run_baseline), outside the frozen
strategy, without modifying risk/risk_manager.py.

risk/risk_manager.py is NOT used by this module and is left untouched.
Its default risk_per_trade_pct (0.01), its rounding to 4 decimals, and its
lack of fee/slippage modeling do not match the authoritative baseline and
were never the thing that produced the 179-trade full-dataset result.
Differences (traced during review):

    | field                | authoritative (WFO)          | risk_manager.py            |
    |----------------------|-------------------------------|-----------------------------|
    | risk %               | 0.02                           | 0.01 (default)              |
    | entry price used     | slippage-adjusted open         | raw signal.entry_price      |
    | field read for stop  | signal.initial_stop            | signal.stop_loss (absent)   |
    | quantity rounding    | none                            | round(qty, 4)                |
    | fees                 | modeled (entry + exit, 0.0006) | not modeled                  |
    | slippage             | modeled (0.0002 in + out)      | not modeled                  |
    | stop_distance <= 0   | trade skipped entirely         | returns 0.0 size             |

Traced directly from walk_forward_full_dataset.py run_baseline():

    risk_amount   = balance * RISK_PCT
    entry_price   = raw_open * (1 + SLIPPAGE_RATE)
    stop_distance = entry_price - initial_stop
    quantity      = risk_amount / stop_distance      # NOT rounded
    entry_fee     = entry_price * quantity * FEE_RATE
    ...
    exit_price    = raw_exit_price * (1 - SLIPPAGE_RATE)
    gross_pnl     = (exit_price - entry_price) * quantity
    exit_fee      = exit_price * quantity * FEE_RATE
    net_pnl       = gross_pnl - exit_fee
"""

from __future__ import annotations

from dataclasses import dataclass

RISK_PCT = 0.02
FEE_RATE = 0.0006
SLIPPAGE_RATE = 0.0002


@dataclass(frozen=True)
class SizedEntry:
    entry_price: float  # slippage-adjusted
    quantity: float
    risk_amount: float
    entry_fee: float
    stop_distance: float


@dataclass(frozen=True)
class SizedExit:
    exit_price: float  # slippage-adjusted
    gross_pnl: float
    exit_fee: float
    net_pnl: float


class NoTradeError(RuntimeError):
    """
    Raised when stop_distance <= 0. The baseline treats this as 'no trade
    taken at all' (the entry candle is simply skipped) -- NOT a zero-size
    position. Callers must not open a position when this is raised.
    """


def size_entry(
    balance: float,
    raw_open_price: float,
    initial_stop: float,
    risk_pct: float = RISK_PCT,
    fee_rate: float = FEE_RATE,
    slippage_rate: float = SLIPPAGE_RATE,
) -> SizedEntry:
    entry_price = raw_open_price * (1 + slippage_rate)
    stop_distance = entry_price - initial_stop
    if stop_distance <= 0:
        raise NoTradeError(
            f"stop_distance <= 0 (entry_price={entry_price}, "
            f"initial_stop={initial_stop}) -- baseline skips this trade "
            "entirely rather than sizing it."
        )
    risk_amount = balance * risk_pct
    quantity = risk_amount / stop_distance  # deliberately unrounded
    entry_fee = entry_price * quantity * fee_rate
    return SizedEntry(
        entry_price=entry_price,
        quantity=quantity,
        risk_amount=risk_amount,
        entry_fee=entry_fee,
        stop_distance=stop_distance,
    )


def size_exit(
    raw_exit_price: float,
    entry_price: float,
    quantity: float,
    fee_rate: float = FEE_RATE,
    slippage_rate: float = SLIPPAGE_RATE,
) -> SizedExit:
    exit_price = raw_exit_price * (1 - slippage_rate)
    gross_pnl = (exit_price - entry_price) * quantity
    exit_fee = exit_price * quantity * fee_rate
    net_pnl = gross_pnl - exit_fee
    return SizedExit(
        exit_price=exit_price,
        gross_pnl=gross_pnl,
        exit_fee=exit_fee,
        net_pnl=net_pnl,
    )
