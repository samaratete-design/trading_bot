import json
import urllib.request

url = "http://127.0.0.1:5000/webhook"
payload = {
    "timestamp": "2026-09-02T04:00:00",
    "open": 50000.0,
    "high": 50500.0,
    "low": 49500.0,
    "close": 50200.0,
    "volume": 1200.0
}

data = json.dumps(payload).encode("utf-8")
req = urllib.request.Request(
    url, 
    data=data, 
    headers={"Content-Type": "application/json"}
)

try:
    with urllib.request.urlopen(req) as response:
        result = response.read().decode("utf-8")
        print("Response from Server:", result)
except Exception as e:
    print("Error connecting to server:", e)

