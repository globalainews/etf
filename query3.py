import sqlite3

conn = sqlite3.connect('D:/claude projects/ETF/etf.db')
c = conn.cursor()

# 红利低波 codes
codes = ['159117','159118','159220','159228','159296','159307','159355','159525','159545','159547','159549','159569','512890','159083']
codes_str = ','.join(["'%s'" % c for c in codes])

# Get scale snapshot
sql = "SELECT fs.code, f.name, fs.shares, fs.date FROM fund_scale fs JOIN funds f ON fs.code=f.code WHERE fs.code IN (%s) AND fs.date=(SELECT MAX(date) FROM fund_scale WHERE code=fs.code) ORDER BY fs.code" % codes_str

print('=== LATEST SCALE SNAPSHOT ===')
for r in c.execute(sql):
    print('  %s  %s  Shares=%.2f  Date=%s' % (r[0], r[1], r[2], r[3]))

conn.close()
