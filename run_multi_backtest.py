from __future__ import annotations

import logging
from execution.broker_interface import PaperTradingBroker
from risk.risk_manager import RiskManager
from runtime.event_bus import EventBus
from runtime.events import OrderExecutedEvent
from runtime.engine import TradingEngine
from strategies.ma_crossover_strategy import MovingAverageCrossoverStrategy
from data.loader import CsvDataLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s -%(message)s")
logger = logging.getLogger("MultiBacktestRunner")

def run_simulation(symbol: str, data_path: str) -> None:
    logger.info(f"=== Starting Backtest Simulation for {symbol} ===")

    bus = EventBus()
    broker = PaperTradingBroker(initial_balance=10_000.0)
    risk_manager = RiskManager(risk_per_trade_pct=0.02)

    strategy = MovingAverageCrossoverStrategy(
        symbol=symbol,
        fast_period=5,
        slow_period=20,
        stop_loss_pct=0.01,
        risk_reward_ratio=2.0,
    )

    engine = TradingEngine(
        strategy=strategy,
        broker=broker,
        risk_manager=risk_manager,
        event_bus=bus,
    )

    bus.subscribe(
        OrderExecutedEvent,
        lambda e: logger.info(
            f"[{symbol}] Executed -> ID: {e.order_result.order_id[:8]} | "
            f"Type: {e.order_result.order_type.name} | "
            f"Price: {e.order_result.filled_price} | Size: {e.order_result.size}"
        ),
    )

    loader = CsvDataLoader(data_path)
    candle_count = 0

    for candle in loader.load_candles():
        engine.process_candle(candle)
        candle_count += 1

    logger.info(f"[{symbol}] Completed. Processed {candle_count} candles.")
    logger.info(f"[{symbol}] Final Account Balance: {broker.get_account_balance():.5f} USD")
    logger.info(f"[{symbol}] Open Positions Left: {len(broker.get_open_positions())}\n")

def main() -> int:
    assets = [
        ("BTCUSDT", "data/historical_sample.csv"),
        ("XAUUSD", "data/XAUUSD_sample.csv"),
        ("EURUSD", "data/EURUSD_sample.csv"),
    ]

    for symbol, path in assets:
        try:
            run_simulation(symbol, path)
        except Exception as e:
            logger.error(f"Error running simulation for {symbol}: {e}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())

