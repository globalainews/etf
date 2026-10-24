import sqlite3

conn = sqlite3.connect('D:/claude projects/ETF/etf.db')
c = conn.cursor()

codes = ['159117','159118','159220','159228','159296','159307','159355','159525','159545','159547','159549','159569','512890','159083']

print('=== 红利低波 ETF: Last 60 Days Price Range Summary ===')
print()
print(f'{"Code":<10} {"Name":<35} {"Latest":>8} {"Max":>8} {"Min":>8} {"Change":>8} {"Days":>5}')
print('-' * 85)

for code in codes:
    c.execute("SELECT name FROM funds WHERE code=?", (code,))
    name = c.fetchone()[0]
    
    c.execute("SELECT MIN(close), MAX(close), close FROM fund_price WHERE code=? ORDER BY date ASC LIMIT 1", (code,))
    row1 = c.fetchone()
    c.execute("SELECT close FROM fund_price WHERE code=? ORDER BY date DESC LIMIT 1", (code,))
    latest = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM fund_price WHERE code=?", (code,))
    days = c.fetchone()[0]
    
    if row1 and latest:
        low, high = row1[0], row1[1]
        chg = ((latest - low) / low * 100) if low else 0
        print('%s  %s  %.4f  %.4f  %.4f  %+6.1f%%  %d' % (code, name[:35], latest, high, low, chg, days))

# Also: total funds in database
c.execute("SELECT COUNT(*) FROM funds")
print()
print('Total funds in database:', c.fetchone()[0])

# Funds matching different query patterns
patterns = [
    ('红利低波 (exact)', "name LIKE '%红利低波%'"),
    ('红利低波/红利/低波', "name LIKE '%红利%' OR name LIKE '%低波%' OR theme LIKE '%红利%'"),
    ('红利 (name or theme)', "name LIKE '%红利%' OR theme LIKE '%红利%'"),
    ('红利 (name only)', "name LIKE '%红利%'"),
    ('红利 (theme only)', "theme LIKE '%红利%'"),
    ('红利/价值 (theme)', "theme LIKE '%红利/价值%'"),
]
print()
print('Query Pattern Counts:')
for label, sql in patterns:
    c.execute("SELECT COUNT(*) FROM funds WHERE %s" % sql)
    print('  %-25s: %d' % (label, c.fetchone()[0]))

conn.close()
