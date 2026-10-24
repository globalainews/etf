# -*- coding: utf-8 -*-
"""给基金规模.xlsx 增加"发行年份"列

逻辑：对每只基金，取其在数据中最早出现日期的年份作为发行年份。
参考 new_etfs_2026_both.csv 交叉验证2026年新基金。
"""

import os
import sys
import shutil
from datetime import datetime

import pandas as pd

EXCEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '基金规模.xlsx')
BACKUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '基金规模_backup_before_year.xlsx')
CSV_2026 = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'new_etfs_2026_both.csv')


def main():
    print("=" * 60)
    print("给基金规模.xlsx 增加发行年份列")
    print("=" * 60)

    # 1. 备份
    if not os.path.exists(BACKUP_FILE):
        print(f"\n备份原文件 → {BACKUP_FILE}")
        shutil.copy2(EXCEL_FILE, BACKUP_FILE)
    else:
        print(f"\n备份已存在，跳过: {BACKUP_FILE}")

    # 2. 读取Excel
    print(f"\n读取 {EXCEL_FILE} ...")
    xl = pd.ExcelFile(EXCEL_FILE)
    sheet_name = xl.sheet_names[0]
    df = pd.read_excel(EXCEL_FILE, sheet_name=sheet_name, dtype={'基金代码': str})
    df['日期'] = pd.to_datetime(df['日期'])
    print(f"  行数: {len(df)}, 基金数: {df['基金代码'].nunique()}")
    print(f"  列: {list(df.columns)}")

    # 3. 如果已有发行年份列，先移除
    if '发行年份' in df.columns:
        print("\n检测到已有'发行年份'列，移除后重新计算...")
        df = df.drop(columns=['发行年份'])

    # 4. 计算每只基金的最早日期 → 发行年份
    print("\n计算每只基金的发行年份...")
    issue_year = df.groupby('基金代码')['日期'].min().reset_index()
    issue_year.columns = ['基金代码', '最早日期']
    issue_year['发行年份'] = issue_year['最早日期'].dt.year
    print(f"  年份分布:")
    for yr, cnt in issue_year['发行年份'].value_counts().sort_index().items():
        print(f"    {yr}: {cnt} 只")

    # 5. 合并回原表
    df = df.merge(issue_year[['基金代码', '发行年份']], on='基金代码', how='left')
    print(f"\n合并后列: {list(df.columns)}")
    print(f"  发行年份空值: {df['发行年份'].isna().sum()}")

    # 6. 参考 CSV 核实2026新基金
    if os.path.exists(CSV_2026):
        print(f"\n读取 {CSV_2026} ...")
        csv_df = pd.read_csv(CSV_2026, dtype=str)
        csv_codes = set(csv_df['基金代码'].tolist())
        print(f"  CSV中2026年新基金: {len(csv_codes)} 只")

        # 检查：2026新基金在Excel中的最早日期
        excel_2026 = issue_year[issue_year['基金代码'].isin(csv_codes)]
        if len(excel_2026) > 0:
            not_2026 = excel_2026[excel_2026['发行年份'] != 2026]
            if len(not_2026) > 0:
                print(f"  ⚠ 以下基金在CSV标记为2026新发，但Excel最早日期不是2026年:")
                for _, row in not_2026.iterrows():
                    print(f"    {row['基金代码']}: Excel最早={row['最早日期'].strftime('%Y-%m-%d')}")
        missing = csv_codes - set(issue_year['基金代码'].tolist())
        if missing:
            print(f"  ⚠ CSV中有 {len(missing)} 只基金在Excel中不存在")

    # 7. 写回Excel
    print(f"\n写入 {EXCEL_FILE} ...")
    # 确保列顺序合理
    cols = ['日期', '基金代码', '基金简称', '类型', '基金规模(份)', '发行年份', '市场']
    df = df[cols]
    df.to_excel(EXCEL_FILE, sheet_name=sheet_name, index=False)
    print(f"  完成! {len(df)} 行, {df['基金代码'].nunique()} 只基金")
    print(f"  发行年份列已添加，列顺序: {cols}")

    # 8. 验证
    print("\n验证:")
    verify = pd.read_excel(EXCEL_FILE, dtype={'基金代码': str})
    print(f"  列: {list(verify.columns)}")
    print(f"  发行年份分布:")
    vy = verify.groupby('基金代码')['发行年份'].first()
    for yr, cnt in vy.value_counts().sort_index().items():
        print(f"    {yr}: {cnt} 只")
    print("\n✅ 完成!")


if __name__ == '__main__':
    main()
