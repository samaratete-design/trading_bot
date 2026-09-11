from strategies.btc_trend_v1 import BTCTrendV1Strategy
from execution.broker_interface import PaperTradingBroker
from risk.risk_manager import RiskManager
from data.loader import CsvDataLoader

print("="*60)
print("🚀 بدء محاكاة التداول التجريبي (النسخة النهائية - 15,000 شمعة)...")
print("="*60)

strategy = BTCTrendV1Strategy()
broker = PaperTradingBroker(initial_balance=10_000.0, single_position_per_symbol=True)
risk_manager = RiskManager()

loader = CsvDataLoader("fixtures/btc_15m_full.csv")
candles = list(loader.load_candles())[-15000:]  # عينة 15,000 شمعة

initial_balance = broker.get_account_balance()
print(f"[💰] الرصيد الابتدائي الافتراضي: ${initial_balance:,.2f}")
print(f"[📊] إجمالي الشموع المختبرة: {len(candles)}")
print("-" * 60)

trades_executed = 0
all_closed_trades = []

class EnhancedSignal:
    def __init__(self, original_signal):
        object.__setattr__(self, '_original_signal', original_signal)
        for field_name in original_signal.__dataclass_fields__:
            object.__setattr__(self, field_name, getattr(original_signal, field_name))
        object.__setattr__(self, 'stop_loss', getattr(original_signal, 'initial_stop', 0.0))

    def __getattr__(self, name):
        if name in ['order_type', 'type']:
            return 'MARKET'
        if name in ['take_profit', 'tp']:
            return None
        return None

for idx, candle in enumerate(candles):
    # تحديث الوسيط وجمع الصفقات المغلقة في هذه الشمعة إن وجدت
    if hasattr(broker, "update_on_candle"):
        newly_closed = broker.update_on_candle(candle)
        if newly_closed:
            all_closed_trades.extend(newly_closed)
    
    strategy.on_closed_candle(candle)
    
    if not broker._open_positions:
        signal = strategy.evaluate_entry()
        if signal:
            enhanced_signal = EnhancedSignal(signal)
            size = risk_manager.calculate_position_size(broker.get_account_balance(), enhanced_signal)
            result = broker.execute_order(enhanced_signal, size)
            if result and hasattr(result, 'status') and result.status.name == "FILLED":
                trades_executed += 1

final_balance = broker.get_account_balance()
net_profit = final_balance - initial_balance
roi = (net_profit / initial_balance) * 100

winning_trades = [t for t in all_closed_trades if getattr(t, 'pnl', 0) > 0]
losing_trades = [t for t in all_closed_trades if getattr(t, 'pnl', 0) <= 0]
win_rate = (len(winning_trades) / len(all_closed_trades) * 100) if all_closed_trades else 0.0

print("="*60)
print("🏁 تقرير الأداء الختامي والشامل (TEST E - 15,000 شمعة)")
print("="*60)
print(f"[📈] إجمالي الصفقات المنفذة: {trades_executed}")
print(f"[🔒] إجمالي الصفقات المغلقة: {len(all_closed_trades)}")
print(f"[🎯] الصفقات الرابحة / الخاسرة: {len(winning_trades)} رابحة / {len(losing_trades)} خاسرة")
print(f"[🏆] نسبة النجاح (Win Rate): {win_rate:.2f}%")
print(f"[💰] الرصيد الابتدائي: ${initial_balance:,.2f}")
print(f"[💰] الرصيد النهائي: ${final_balance:,.2f}")
print(f"[📊] صافي الربح / الخسارة (Net PnL): ${net_profit:,.2f} ({roi:+.2f}%)")
print("="*60)
