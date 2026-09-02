import requests

url = "http://127.0.0.1:5000/webhook"
payload = {
    "timestamp": "2026-09-02T04:00:00",
    "open": 50000.0,
    "high": 50500.0,
    "low": 49500.0,
    "close": 50200.0,
    "volume": 1200.0
}
response = requests.post(url, json=payload)
print("Status Code:", response.status_code)
print("Response:", response.json())

