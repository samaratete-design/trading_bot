from __future__ import annotations

import logging
from execution.broker_interface import PaperTradingBroker
from risk.risk_manager import RiskManager
from runtime.event_bus import EventBus
from runtime.events import OrderExecutedEvent
from runtime.engine import TradingEngine
from strategies.ma_crossover_strategy import MovingAverageCrossoverStrategy
from data.loader import CsvDataLoader

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("BacktestRunner")

SYMBOL = "BTCUSDT"


def main() -> int:
    logger.info(f"Starting Backtest Simulation with MA Crossover Strategy for {SYMBOL}...")

    bus = EventBus()
    # تفعيل وضع صفقة واحدة لكل رمز كشبكة أمان
    broker = PaperTradingBroker(initial_balance=10_000.0, single_position_per_symbol=True)
    risk_manager = RiskManager(risk_per_trade_pct=0.01)
    
    strategy = MovingAverageCrossoverStrategy(
        symbol=SYMBOL,
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
            f"Executed Order -> ID: {e.order_result.order_id[:8]} | "
            f"Symbol: {e.order_result.symbol} | "
            f"Type: {e.order_result.order_type.name} | "
            f"Price: {e.order_result.filled_price} | "
            f"Size: {e.order_result.size}"
        ),
    )

    loader = CsvDataLoader("data/historical_sample.csv")
    candle_count = 0

    for candle in loader.load_candles():
        engine.process_candle(candle)
        candle_count += 1

    logger.info(f"Backtest completed. Processed {candle_count} candles.")
    logger.info(f"Final Account Balance: {broker.get_account_balance()} USD")
    logger.info(f"Open Positions Left: {len(broker.get_open_positions())}")
    return 0


if __name__ == "__main__":
    main()
