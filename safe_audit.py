from webhook_server import broker

print("\n" + "="*40)
print("         SAFE TRADE AUDIT REPORT        ")
print("="*40)

initial_balance = 10000.0
current_balance = broker.get_account_balance()
net_pnl = current_balance - initial_balance

print(f"Initial Balance     : {initial_balance:,.2f} USD")
print(f"Current Balance     : {current_balance:,.2f} USD")
print(f"Realized P&L        : {net_pnl:,.2f} USD")

open_positions = broker.get_open_positions()
print(f"Remaining Positions : {len(open_positions)}")

if open_positions:
    for idx, pos in enumerate(open_positions, 1):
        print(f"  [{idx}] Symbol: {pos.symbol} | Type: {pos.order_type.name} | Entry: {pos.entry_price} | Size: {pos.size}")
else:
    print("  -> All positions closed successfully (Lifecycle complete).")

print("="*40 + "\n")

