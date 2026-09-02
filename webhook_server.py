from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from flask import Flask, jsonify, request

from execution.broker_interface import PaperTradingBroker
from risk.risk_manager import RiskManager
from runtime.engine import TradingEngine
from runtime.event_bus import EventBus
from runtime.events import OrderExecutedEvent
from strategies.ma_crossover_strategy import MovingAverageCrossoverStrategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("WebhookServer")

app = Flask(__name__)

@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

# إعداد محرك التداول للبوت
bus = EventBus()
broker = PaperTradingBroker(initial_balance=10_000.0)
risk_manager = RiskManager(risk_per_trade_pct=0.02)

strategy = MovingAverageCrossoverStrategy(
    symbol="BTCUSDT",
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
        f"[LIVE] Executed -> Type: {e.order_result.order_type.name} | "
        f"Price: {e.order_result.filled_price} | Size: {e.order_result.size}"
    ),
)

@app.route("/webhook", methods=["POST"])
def receive_market_data():
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400

        candle = Candle(
            timestamp=datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat())),
            open=float(data["open"]),
            high=float(data["high"]),
            low=float(data["low"]),
            close=float(data["close"]),
            volume=float(data.get("volume", 1000.0))
        )

        engine.process_candle(candle)
        current_balance = broker.get_account_balance()
        logger.info(f"Processed live candle. Current Balance: {current_balance:.2f} USD")

        return jsonify({
            "status": "success",
            "message": "Candle processed successfully",
            "balance": current_balance
        }), 200

    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    logger.info("Starting Live Webhook Trading Server on port 5000...")
    app.run(host="0.0.0.0", port=5000)

