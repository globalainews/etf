import sqlite3

conn = sqlite3.connect(r'D:\claude projects\ETF\etf.db')
c = conn.cursor()

print("=== QUERY 1: Funds with 红利/低波 in name or theme ===")
c.execute("SELECT code, name, type, market, theme, issue_year FROM funds WHERE name LIKE '%红利%' OR name LIKE '%低波%' OR theme LIKE '%红利%'")
rows = c.fetchall()
print("Found " + str(len(rows)) + " funds")
print()
print("Code       Name                              Type      Mkt  Year  Theme")
print("-" * 120)
for r in rows:
    print(f"{r[0]:<10} {r[1]:<35} {r[2]:<10} {r[3]:<5} {str(r[5]):<5} {r[4]}")

conn.close()
