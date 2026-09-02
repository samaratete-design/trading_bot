from webhook_server import broker, engine

print("=== ACCOUNT STATUS & TRADE RECORDS ===")
print(f"Current Balance: {broker.get_account_balance():.2f} USD")
print(f"Active Positions: {broker.get_open_positions()}")
print(f"Trade History: {broker.get_trade_history()}")

