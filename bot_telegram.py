import time
import logging
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

RISK_PERCENT = 0.02
SL_PERCENT = 0.01
RR_RATIO = 2.0
STARTING_BALANCE = 10.0

def fetch_closes(symbol: str, interval: str, limit: int) -> list:
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1d", interval=interval)
        closes = data['Close'].dropna().tolist()
        return closes[-limit:]
    except Exception as e:
        logger.error(f"Error fetching data from Yahoo Finance: {e}")
        return []

def calculate_sma(data, period):
    if len(data) < period:
        return None
    return sum(data[-period:]) / period

def open_position(direction, entry_price, balance):
    risk_amount = balance * RISK_PERCENT
    sl_distance = entry_price * SL_PERCENT
    size = risk_amount / sl_distance

    if direction == "BUY":
        sl = entry_price - sl_distance
        tp = entry_price + (sl_distance * RR_RATIO)
    else:
        sl = entry_price + sl_distance
        tp = entry_price - (sl_distance * RR_RATIO)

    position = {
        "direction": direction,
        "entry_price": entry_price,
        "size": size,
        "sl": sl,
        "tp": tp,
    }

    dir_icon = "🟢" if direction == "BUY" else "🔴"
    
    msg = (
        f"{dir_icon} **{'شراء (BUY)' if direction == 'BUY' else 'بيع (SELL)'} SIGNAL - Open**\n"
        f"Symbol: {SYMBOL}\n"
        f"Entry: `{entry_price:.2f}`\n"
        f"Size: `{size:.4f}`\n"
        f"SL: `{sl:.2f}`\n"
        f"TP: `{tp:.2f}`\n"
        f"Risk: {risk_amount:.2f} USD ({RISK_PERCENT*100:.0f}%)"
    )
    logger.info(msg.replace("\n", " | "))
    send_telegram_alert(msg)
    return position

def close_position(position, exit_price, balance, reason):
    if position["direction"] == "BUY":
        pnl = (exit_price - position["entry_price"]) * position["size"]
    else:
        pnl = (position["entry_price"] - exit_price) * position["size"]

    new_balance = balance + pnl
    dir_icon = "🟢" if position["direction"] == "BUY" else "🔴"

    msg = (
        f"🏁 **Position Closed ({reason})** {dir_icon}\n"
        f"Symbol: {SYMBOL}\n"
        f"Direction: {position['direction']}\n"
        f"Entry: `{position['entry_price']:.2f}`\n"
        f"Exit: `{exit_price:.2f}`\n"
        f"Realized P&L: `{pnl:.2f} USD`\n"
        f"New Balance: `{new_balance:.2f} USD`"
    )
    logger.info(msg.replace("\n", " | "))
    send_telegram_alert(msg)
    return new_balance

def check_sl_tp(position, current_price):
    if position["direction"] == "BUY":
        if current_price <= position["sl"]:
            return "SL"
        if current_price >= position["tp"]:
            return "TP"
    else:
        if current_price >= position["sl"]:
            return "SL"
        if current_price <= position["tp"]:
            return "TP"
    return None

def main():
    logger.info("Starting Telegram Signal Bot...")
    send_telegram_alert(
        f"🚀 **Bot Started Successfully (Test Mode 10$)!** Monitoring {SYMBOL}\n"
        f"Starting Balance: {STARTING_BALANCE:.2f} USD"
    )

    balance = STARTING_BALANCE
    last_signal = None
    position = None

    while True:
        try:
            closes = fetch_closes(SYMBOL, INTERVAL, LIMIT)
            if len(closes) >= 20:
                fast_sma = calculate_sma(closes, 5)
                slow_sma = calculate_sma(closes, 20)
                current_price = closes[-1]

                logger.info(
                    f"Price: {current_price} | Fast SMA: {fast_sma:.2f} | "
                    f"Slow SMA: {slow_sma:.2f} | Balance: {balance:.2f} | "
                    f"Position: {position['direction'] if position else 'None'}"
                )

                if position is not None:
                    hit = check_sl_tp(position, current_price)
                    if hit:
                        balance = close_position(position, current_price, balance, hit)
                        position = None

                new_signal = None
                if fast_sma > slow_sma:
                    new_signal = "BUY"
                elif fast_sma < slow_sma:
                    new_signal = "SELL"

                if new_signal is not None and new_signal != last_signal:
                    if position is not None and position["direction"] != new_signal:
                        balance = close_position(position, current_price, balance, "REVERSE")
                        position = None

                    if position is None:
                        position = open_position(new_signal, current_price, balance)
                        last_signal = new_signal

            time.sleep(30)

        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()
