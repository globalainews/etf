import sqlite3
import json

conn = sqlite3.connect(r'D:\claude projects\ETF\etf.db')
c = conn.cursor()

# ============ QUERY: All 红利低波 ETFs ============
print("=" * 120)
print("SPECIFIC 红利低波 (Dividend Low Volatility) ETFs")
print("=" * 120)

c.execute("SELECT code, name, type, market, theme, issue_year FROM funds WHERE name LIKE '%红利低波%'")
rows = c.fetchall()
print(f"\nFound {len(rows)} funds with exact '红利低波' in name:\n")
print(f'{"Code":<10} {"Name":<40} {"Type":<10} {"Market":<10} {"Year":<5} {"Theme"}')
print("-" * 95)
for r in rows:
    print(f'{r[0]:<10} {r[1]:<40} {r[2]:<10} {r[3]:<10} {str(r[5]):<5} {r[4]}')

# Save codes for price/scale query
codes = [r[0] for r in rows]
print(f"\n\nThese codes will be queried for price/scale: {codes}")

# ============ Get latest date from fund_price ============
c.execute("SELECT MAX(date) FROM fund_price")
latest = c.fetchone()[0]
print(f"\nLatest available date in fund_price: {latest}")

# ============ Query price and scale for each 红利低波 ETF ============
print("\n" + "=" * 120)
print("PRICE AND SCALE DATA FOR EACH 红利低波 ETF (LAST 60 TRADING DAYS)")
print("=" * 120)

for i, code in enumerate(codes):
    name = [r[1] for r in rows if r[0] == code][0]
    
    # Get last 60 days price
    c.execute("SELECT date, close FROM fund_price WHERE code = ? ORDER BY date DESC LIMIT 60", (code,))
    prices = c.fetchall()
    
    # Get last 60 days scale
    c.execute("SELECT date, shares FROM fund_scale WHERE code = ? ORDER BY date DESC LIMIT 60", (code,))
    scales = c.fetchall()
    
    print(f"\n--- {code} {name} ---")
    print(f"{'Date':<12} {'Close Price':>12} {'  Scale (shares)':>20}")
    print("-" * 45)
    
    # Merge by date
    price_dict = {p[0]: p[1] for p in prices}
    scale_dict = {s[0]: s[1] for s in scales}
    all_dates = sorted(set(list(price_dict.keys()) + list(scale_dict.keys())), reverse=True)
    all_dates = all_dates[:60]
    
    for d in all_dates:
        close = f"{price_dict[d]:.4f}" if d in price_dict else "N/A"
        shares = f"{scale_dict[d]:.2f}" if d in scale_dict else "N/A"
        print(f"{d:<12} {close:>12} {shares:>20}")

conn.close()
