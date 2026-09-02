import time
import logging
from datetime import datetime
import os
import requests
import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
)
logger = logging.getLogger("TelegramBot")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=5)
        return response.json()
    except Exception as e:
        logger.error(f"Telegram Error: {e}")

SYMBOL = "BTC-USD"
INTERVAL = "1m"
LIMIT = 50

def fetch_closes(symbol: str, interval: str, limit: int) -> list:
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1d", interval=interval)
        closes = data["Close"].dropna().tolist()
        return closes[-limit:]
    except Exception as e:
        logger.error(f"Error fetching data from Yahoo Finance: {e}")
        return []

def calculate_sma(data, period):
    if len(data) < period:
        return None
    return sum(data[-period:]) / period

def main():
    logger.info("Starting Telegram Signal Bot...")
    send_telegram_alert("Bot Started Successfully! Monitoring BTCUSDT...")

    last_signal = None

    while True:
        try:
            closes = fetch_closes(SYMBOL, INTERVAL, LIMIT)
            if len(closes) >= 20:
                fast_sma = calculate_sma(closes, 5)
                slow_sma = calculate_sma(closes, 20)
                current_price = closes[-1]

                logger.info(f"Price: {current_price} | Fast SMA: {fast_sma:.2f} | Slow SMA: {slow_sma:.2f}")

                if fast_sma > slow_sma and last_signal != "BUY":
                    msg = f"BUY SIGNAL\nSymbol: {SYMBOL}\nPrice: {current_price}\nFast SMA crossed above Slow SMA!"
                    send_telegram_alert(msg)
                    last_signal = "BUY"
                elif fast_sma < slow_sma and last_signal != "SELL":
                    msg = f"SELL SIGNAL\nSymbol: {SYMBOL}\nPrice: {current_price}\nFast SMA crossed below Slow SMA!"
                    send_telegram_alert(msg)
                    last_signal = "SELL"

            time.sleep(30)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
