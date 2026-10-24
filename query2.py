import sqlite3

conn = sqlite3.connect('D:/claude projects/ETF/etf.db')
c = conn.cursor()

print('=== BROADER: 红利 related funds ===')
c.execute("SELECT code, name, type, market, theme, issue_year FROM funds WHERE name LIKE '%红利%' OR theme LIKE '%红利%'")
rows = c.fetchall()
print('Total:', len(rows))

print('Theme distribution:')
themes = {}
for r in rows:
    t = r[4] or '(empty)'
    themes[t] = themes.get(t, 0) + 1
for t, cnt in sorted(themes.items(), key=lambda x: -x[1]):
    print('  %s: %d' % (t, cnt))

print()
print('=== COUNT OF 红利/价值 THEMED ETFs ===')
c.execute("SELECT theme, COUNT(*) FROM funds WHERE theme LIKE '%红利%' OR theme LIKE '%价值%' GROUP BY theme ORDER BY COUNT(*) DESC")
for r in c.fetchall():
    print('  %s: %d' % (r[0], r[1]))

c.execute("SELECT COUNT(*) FROM funds WHERE theme LIKE '%红利%'")
print('Funds with 红利 in theme:', c.fetchone()[0])

c.execute("SELECT COUNT(*) FROM funds WHERE theme LIKE '%价值%'")
print('Funds with 价值 in theme:', c.fetchone()[0])

c.execute("SELECT COUNT(*) FROM funds WHERE theme LIKE '%红利%' OR theme LIKE '%价值%'")
print('Funds with 红利 or 价值 in theme:', c.fetchone()[0])

c.execute("SELECT COUNT(*) FROM funds WHERE theme LIKE '%红利/价值%'")
print('Funds with 红利/价值 in theme:', c.fetchone()[0])

# 红利低波 summary
print()
print('=== 红利低波 ETF Summary ===')
c.execute("SELECT code, name, type, market, issue_year FROM funds WHERE name LIKE '%红利低波%'")
rows2 = c.fetchall()
print('Total 红利低波 ETFs:', len(rows2))
for r in rows2:
    print('  %s  %s  (%s  %s  %s)' % (r[0], r[1], r[2], r[3], r[4]))

# Latest price snapshot
print()
print('=== LATEST PRICE SNAPSHOT (most recent date) ===')
c.execute("SELECT MAX(date) FROM fund_price")
maxdate = c.fetchone()[0]
print('Latest date:', maxdate)

codes_str = ','.join(["'%s'" % r[0] for r in rows2])
sql = "SELECT fp.code, f.name, fp.close, fp.date FROM fund_price fp JOIN funds f ON fp.code=f.code WHERE fp.code IN (%s) AND fp.date=(SELECT MAX(date) FROM fund_price WHERE code=fp.code) ORDER BY fp.code" % codes_str
for r in c.execute(sql):
    print('  %s  %s  Price=%.4f  Date=%s' % (r[0], r[1], r[2], r[3]))

# Also get latest scale
sql2 = "SELECT fp.code, f.name, fs.shares, fs.date FROM fund_scale fs JOIN funds f ON fs.code=f.code WHERE fs.code IN (%s) AND fs.date=(SELECT MAX(date) FROM fund_scale WHERE code=fs.code) ORDER BY fs.code" % codes_str
print()
print('=== LATEST SCALE SNAPSHOT ===')
for r in c.execute(sql2):
    print('  %s  %s  Shares=%.2f  Date=%s' % (r[0], r[1], r[2], r[3]))

conn.close()
