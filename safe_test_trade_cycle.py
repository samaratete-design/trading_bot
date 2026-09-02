import json
import urllib.request
from datetime import datetime, timedelta

URL = "http://127.0.0.1:5000/webhook"
BASE_TIME = datetime(2026, 9, 2, 5, 0, 0)

def create_candle(time_offset_hours, open_p, high_p, low_p, close_p, volume=1000.0):
    t = BASE_TIME + timedelta(hours=time_offset_hours)
    return {
        "timestamp": t.isoformat(),
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": volume
    }

print("[-] Building safe candle sequence...")
candles = []

# 1. Warm-up candles (22 candles) to stabilize indicators around 50,000
for i in range(22):
    candles.append(create_candle(i, 50000.0, 50100.0, 49900.0, 50000.0))

# 2. Golden Cross Trigger Candle (Index 22 -> 23rd candle) -> Triggers BUY at ~52,000
candles.append(create_candle(22, 50000.0, 52500.0, 50000.0, 52000.0, volume=3000.0))

# 3. Post-Entry Candles designed to force Take Profit (e.g., soaring to 56,000)
for i in range(1, 8):
    candles.append(create_candle(22 + i, 52000.0 + (i * 500), 54000.0 + (i * 500), 52000.0, 53500.0 + (i * 500), volume=2000.0))

print(f"[+] Total candles prepared: {len(candles)}")
print("[+] Sending candles to Webhook broker...")

success_count = 0
for idx, candle in enumerate(candles):
    data = json.dumps(candle).encode("utf-8")
    req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            success_count += 1
    except Exception as e:
        print(f"[!] Error at candle {idx + 1}: {e}")

print(f"[+] Sequence completed successfully! Sent {success_count}/{len(candles)} candles.")

