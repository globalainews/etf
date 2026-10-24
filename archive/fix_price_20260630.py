# -*- coding: utf-8 -*-
"""修复 2026-06-30 ETF 价格 —— 从通达信本地 .day 文件读取
1. 读取基金规模.xlsx 中 6月30日有规模数据的基金代码
2. 从 D:\tdx\vipdoc\ 读取对应 .day 文件，提取 2026-06-30 收盘价
3. 清除 etf_prices.csv 中 6月30日旧数据，写入新价格
4. 重建 etf_total_scale.csv
"""
import sys, os, struct, time
from datetime import datetime
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_FILE = os.path.join(SCRIPT_DIR, '基金规模.xlsx')
PRICE_CSV = os.path.join(SCRIPT_DIR, 'etf_prices.csv')
OUTPUT_CSV = os.path.join(SCRIPT_DIR, 'etf_total_scale.csv')
TDX_BASE = r'D:\tdx\vipdoc'
TARGET_DATE_STR = '2026-06-30'
TARGET_DATE_INT = 20260630

RECORD_SIZE = 32
RECORD_FMT = '<IIIIIfII'  # date, open, high, low, close, amount, volume, reserved


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def code_to_day_path(code):
    """基金代码 → 通达信 .day 文件路径"""
    if code.startswith(('51', '56', '58', '588')):
        return os.path.join(TDX_BASE, 'sh', 'lday', f'sh{code}.day')
    elif code.startswith(('15', '16', '18')):
        return os.path.join(TDX_BASE, 'sz', 'lday', f'sz{code}.day')
    return None


def get_close_price(day_path, target_date_int):
    """从 .day 文件提取指定日期的收盘价，未找到返回 None"""
    if not os.path.exists(day_path):
        return None
    with open(day_path, 'rb') as f:
        data = f.read()
    for i in range(0, len(data), RECORD_SIZE):
        if i + RECORD_SIZE > len(data):
            break
        try:
            date, op, hi, lo, cl, amount, vol, reserved = struct.unpack_from(RECORD_FMT, data, i)
            if date == target_date_int and cl > 0:
                return cl / 1000.0
        except Exception:
            continue
    return None


# ============================================================
# Step 1: 获取 6月30日有规模数据的所有基金
# ============================================================
log("=" * 60)
log("修复 2026-06-30 价格（数据来源：通达信本地 .day 文件）")
log("=" * 60)

log(">>> Step 1: 读取基金代码...")
xl = pd.ExcelFile(EXCEL_FILE)
df_scale = pd.read_excel(EXCEL_FILE, sheet_name=xl.sheet_names[0], dtype={'基金代码': str})
df_scale['日期'] = pd.to_datetime(df_scale['日期'])

scale_0630 = df_scale[df_scale['日期'] == TARGET_DATE_STR]
codes = sorted(scale_0630['基金代码'].unique().tolist())
log(f"  2026-06-30 有规模数据: {len(codes)} 只基金")

# ============================================================
# Step 2: 从通达信 .day 文件提取价格
# ============================================================
log(f">>> Step 2: 从通达信提取 {TARGET_DATE_STR} 收盘价...")

prices_found = []
no_file = 0
no_price = 0

for i, code in enumerate(codes):
    day_path = code_to_day_path(code)
    if day_path is None:
        no_file += 1
        continue

    close = get_close_price(day_path, TARGET_DATE_INT)
    if close is not None:
        prices_found.append({'基金代码': code, '日期': TARGET_DATE_STR, '收盘价': close})
    else:
        no_price += 1
        if no_price <= 5:
            log(f"  [MISS] {code} .day 文件中无 {TARGET_DATE_STR} 数据")

    if (i + 1) % 500 == 0:
        log(f"  进度: {i+1}/{len(codes)}, 已找到 {len(prices_found)} 只")

log(f"  提取完成: 有价格 {len(prices_found)} 只, 无文件 {no_file} 只, 无当日数据 {no_price} 只")

if not prices_found:
    log("[FAIL] 没有提取到任何价格！")
    sys.exit(1)

# ============================================================
# Step 3: 更新 etf_prices.csv
# ============================================================
log(">>> Step 3: 更新 etf_prices.csv ...")

df_new = pd.DataFrame(prices_found)
df_new['日期'] = pd.to_datetime(df_new['日期'])

# 读取现有价格，清除 6月30日旧数据
if os.path.exists(PRICE_CSV):
    existing = pd.read_csv(PRICE_CSV, dtype={'基金代码': str})
    existing['日期'] = pd.to_datetime(existing['日期'])
    log(f"  现有价格: {len(existing)} 行")

    old_0630 = existing[existing['日期'] == TARGET_DATE_STR]
    log(f"  旧 2026-06-30 数据: {len(old_0630)} 行, {old_0630['基金代码'].nunique()} 只基金")

    # 备份
    backup = PRICE_CSV.replace('.csv', '_backup_0630.csv')
    existing.to_csv(backup, index=False, encoding='utf-8-sig')
    log(f"  备份: {backup}")

    # 清除旧数据
    existing = existing[existing['日期'] != TARGET_DATE_STR]
    log(f"  清除后: {len(existing)} 行")
else:
    existing = pd.DataFrame(columns=['基金代码', '日期', '收盘价'])
    existing['日期'] = pd.to_datetime(existing['日期'])

# 合并
combined = pd.concat([existing, df_new], ignore_index=True)
combined = combined.drop_duplicates(subset=['基金代码', '日期'], keep='last')
combined = combined.sort_values(['基金代码', '日期']).reset_index(drop=True)

combined.to_csv(PRICE_CSV, index=False, encoding='utf-8-sig')
log(f"  [OK] 写入 {PRICE_CSV}: {len(combined)} 行, {combined['基金代码'].nunique()} 只基金")

# ============================================================
# Step 4: 重建 etf_total_scale.csv
# ============================================================
log(">>> Step 4: 重建总规模/总金额 CSV...")

# 规模数据
scale = df_scale.copy()
scale['基金规模(份)'] = pd.to_numeric(scale['基金规模(份)'], errors='coerce')
scale = scale.dropna(subset=['基金规模(份)'])
cutoff = pd.to_datetime('2025-01-01')
scale = scale[scale['日期'] >= cutoff]

# 按日期汇总总规模
daily_scale = scale.groupby('日期').agg(
    总规模_份=('基金规模(份)', 'sum'),
    基金数量=('基金代码', 'nunique')
).reset_index()
daily_scale['总规模(亿份)'] = (daily_scale['总规模_份'] / 100_000_000).round(2)

# 合并价格计算总金额
prices_df = combined.copy()
merged = scale[['日期', '基金代码', '基金规模(份)']].merge(
    prices_df[['日期', '基金代码', '收盘价']],
    on=['日期', '基金代码'], how='inner'
)
merged['金额(元)'] = merged['基金规模(份)'] * merged['收盘价']

daily_amount = merged.groupby('日期').agg(
    总金额_元=('金额(元)', 'sum'),
    有价基金数=('基金代码', 'nunique')
).reset_index()
daily_amount['总金额(亿元)'] = (daily_amount['总金额_元'] / 100_000_000).round(2)

result = daily_scale.merge(
    daily_amount[['日期', '总金额(亿元)', '有价基金数']],
    on='日期', how='left'
)
result = result.sort_values('日期')
result = result[['日期', '总规模(亿份)', '总金额(亿元)', '基金数量', '有价基金数']]

# 显示 6月30日 修复结果
row = result[result['日期'] == TARGET_DATE_STR]
if len(row) > 0:
    r = row.iloc[0]
    log("")
    log("  ★★★ 2026-06-30 修复结果 ★★★")
    log(f"    总规模:    {r['总规模(亿份)']:>12.2f} 亿份")
    log(f"    总金额:    {r['总金额(亿元)']:>12.2f} 亿元")
    log(f"    基金数量:  {int(r['基金数量']):>12}")
    log(f"    有价基金数: {int(r['有价基金数']) if pd.notna(r['有价基金数']) else 'N/A':>12}")

# 显示前后对比
log("")
log("  近期趋势:")
for _, r in result.tail(8).iterrows():
    amt = f"{r['总金额(亿元)']:>10.0f}" if pd.notna(r['总金额(亿元)']) else "        N/A"
    pfd = f"{int(r['有价基金数']):>4}" if pd.notna(r['有价基金数']) else " N/A"
    marker = " ★" if r['日期'].strftime('%Y-%m-%d') == TARGET_DATE_STR else ""
    log(f"    {r['日期'].strftime('%Y-%m-%d')}  规模: {r['总规模(亿份)']:>10.0f} 亿份  金额: {amt} 亿元  有价: {pfd}{marker}")

# 写入
result['日期'] = result['日期'].dt.strftime('%Y-%m-%d')
result.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig', float_format='%.2f')
log(f"")
log(f"  [OK] 已写入: {OUTPUT_CSV}")
log("=" * 60)
log("修复完成！")
log("=" * 60)
