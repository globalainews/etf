# -*- coding: utf-8 -*-
"""从 基金规模.xlsx + etf_prices.csv 汇总每日 ETF 总规模与总金额，导出 CSV

输出: etf_total_scale.csv
列: 日期, 总规模(亿份), 总金额(亿元), 基金数量, 有价基金数

总金额 = SUM(基金规模 × 收盘价)，仅计算有价格数据的基金

用法:
  F:/anaconda3/python build_total_scale.py           # 全量重建
"""

import sys
import os
from datetime import datetime

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXCEL_FILE = os.path.join(SCRIPT_DIR, '基金规模.xlsx')
PRICE_CSV = os.path.join(SCRIPT_DIR, 'etf_prices.csv')
OUTPUT_CSV = os.path.join(SCRIPT_DIR, 'etf_total_scale.csv')


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_scale_data():
    """加载规模数据，过滤 2025-01-01 起"""
    xl = pd.ExcelFile(EXCEL_FILE)
    df = pd.read_excel(EXCEL_FILE, sheet_name=xl.sheet_names[0], dtype={'基金代码': str})
    df['日期'] = pd.to_datetime(df['日期'])
    df['基金规模(份)'] = pd.to_numeric(df['基金规模(份)'], errors='coerce')
    df = df.dropna(subset=['基金规模(份)'])
    cutoff = pd.to_datetime('2025-01-01')
    df = df[df['日期'] >= cutoff]
    return df


def load_price_data():
    """加载价格数据"""
    if not os.path.exists(PRICE_CSV):
        log(f"  [WARN] 价格文件不存在: {PRICE_CSV}")
        return None
    df = pd.read_csv(PRICE_CSV, dtype={'基金代码': str})
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.drop_duplicates(subset=['基金代码', '日期'], keep='last')
    return df


def main():
    log("=" * 60)
    log("构建 ETF 每日总规模 & 总金额 CSV")
    log("=" * 60)

    # 1. 加载数据
    scale = load_scale_data()
    log(f"规模数据: {len(scale)} 行, {scale['基金代码'].nunique()} 只基金")
    log(f"  日期: {scale['日期'].min().strftime('%Y-%m-%d')} ~ {scale['日期'].max().strftime('%Y-%m-%d')}")

    prices = load_price_data()

    # 2. 按日期汇总总规模（全部基金）
    daily_scale = scale.groupby('日期').agg(
        总规模_份=('基金规模(份)', 'sum'),
        基金数量=('基金代码', 'nunique')
    ).reset_index()
    daily_scale['总规模(亿份)'] = (daily_scale['总规模_份'] / 100_000_000).round(2)

    # 3. 计算总金额（合并价格数据）
    if prices is not None:
        log(f"价格数据: {len(prices)} 行, {prices['基金代码'].nunique()} 只基金")
        log(f"  日期: {prices['日期'].min().strftime('%Y-%m-%d')} ~ {prices['日期'].max().strftime('%Y-%m-%d')}")

        merged = scale[['日期', '基金代码', '基金规模(份)']].merge(
            prices[['日期', '基金代码', '收盘价']],
            on=['日期', '基金代码'],
            how='inner'
        )
        merged['金额(元)'] = merged['基金规模(份)'] * merged['收盘价']
        log(f"  合并后: {len(merged)} 行 (有价格的基金-日期组合)")

        daily_amount = merged.groupby('日期').agg(
            总金额_元=('金额(元)', 'sum'),
            有价基金数=('基金代码', 'nunique')
        ).reset_index()
        daily_amount['总金额(亿元)'] = (daily_amount['总金额_元'] / 100_000_000).round(2)

        # 4. 合并规模与金额
        combined = daily_scale.merge(
            daily_amount[['日期', '总金额(亿元)', '有价基金数']],
            on='日期',
            how='left'
        )
    else:
        combined = daily_scale.copy()
        combined['总金额(亿元)'] = None
        combined['有价基金数'] = None

    combined = combined.sort_values('日期')
    combined = combined[['日期', '总规模(亿份)', '总金额(亿元)', '基金数量', '有价基金数']]

    # 5. 统计
    valid_amount = combined['总金额(亿元)'].dropna()
    log(f"  汇总后: {len(combined)} 个交易日")
    latest = combined.iloc[-1]
    log(f"  最新 ({latest['日期'].strftime('%Y-%m-%d')}): "
        f"规模 {latest['总规模(亿份)']:.0f} 亿份, "
        f"金额 {latest['总金额(亿元)']:.0f} 亿元, "
        f"基金 {int(latest['基金数量'])} 只")
    if pd.notna(latest['总金额(亿元)']):
        log(f"  均单价: {latest['总金额(亿元)'] / latest['总规模(亿份)']:.2f} 元/份")

    # 6. 写入 CSV
    log(f"写入 {OUTPUT_CSV} ...")
    combined['日期'] = combined['日期'].dt.strftime('%Y-%m-%d')
    combined.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig', float_format='%.2f')
    log(f"  [OK] 保存成功! {len(combined)} 行")

    # 7. 最新5天
    log("最新5天:")
    for _, row in combined.tail(5).iterrows():
        amt = f"{row['总金额(亿元)']:>10.0f}" if pd.notna(row['总金额(亿元)']) else "        N/A"
        log(f"  {row['日期']}  规模: {row['总规模(亿份)']:>10.0f} 亿份  金额: {amt} 亿元")


if __name__ == '__main__':
    main()
