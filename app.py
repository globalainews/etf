# -*- coding: utf-8 -*-
"""ETF基金规模趋势分析 - Web应用（数据源: etf.db SQLite）"""
import sys
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime
import pandas as pd
from flask import Flask, jsonify, render_template, request

import db
from src.etf_rankings.ranking_service import (
    RankingInputError,
    RankingNotFoundError,
    build_rankings,
)
from src.etf_rankings.web_api import mini_api

app = Flask(__name__)
app.register_blueprint(mini_api)

def load_data():
    """从 etf.db 加载份额数据 + 基金属性（列名与旧 xlsx 保持一致）"""
    conn = db.connect()
    df = pd.read_sql_query("""
        SELECT s.date       AS 日期,
               s.code       AS 基金代码,
               f.name       AS 基金简称,
               f.type       AS 类型,
               s.shares     AS "基金规模(份)",
               f.issue_year AS 发行年份,
               f.market     AS 市场,
               f.theme      AS 行业主题
        FROM fund_scale s
        JOIN funds f ON f.code = s.code
    """, conn)
    conn.close()
    df['日期'] = pd.to_datetime(df['日期'])
    df['行业主题'] = df['行业主题'].fillna('').astype(str)
    return df

print("正在加载数据...")
DATA = load_data()
print(f"数据加载完成: {len(DATA)} 条记录, {DATA['基金代码'].nunique()} 只基金")

# 提取可用行业主题列表
ALL_THEMES = sorted([t for t in DATA['行业主题'].unique() if t and t != ''])
if ALL_THEMES:
    print(f"行业主题({len(ALL_THEMES)}个): {', '.join(ALL_THEMES)}")
else:
    print("未发现行业主题数据")

# 全部基金数据，按基金代码和日期排序
ALL_DATA = DATA.sort_values(['基金代码', '日期']).copy()
ALL_FUND_CODES = sorted(DATA['基金代码'].unique().tolist())
print(f"全部基金: {len(ALL_FUND_CODES)} 只")

# 加载价格数据（如果存在）
def load_prices():
    """从 etf.db 加载ETF收盘价数据，无数据返回None"""
    try:
        conn = db.connect()
        df = pd.read_sql_query("""
            SELECT code AS 基金代码, date AS 日期, close AS 收盘价
            FROM fund_price
        """, conn)
        conn.close()
        if len(df) == 0:
            print("价格数据为空，价格相关功能将不可用")
            return None
        df['日期'] = pd.to_datetime(df['日期'])
        print(f"价格数据加载完成: {len(df)} 条记录, {df['基金代码'].nunique()} 只基金")
        return df
    except Exception as e:
        print(f"价格数据加载失败: {e}")
        return None

PRICES = load_prices()


# ===================================================================
#  策略标记数据（E2 信号 + 回测买卖点，来自 etf.db 的 signal_events/
#  backtest_trades 表，由 import_markers.py 从研究 CSV 导入）
# ===================================================================
def load_markers():
    """从 signal_events / backtest_trades 表加载标记数据（只读）"""
    try:
        conn = db.connect()
        sig = pd.read_sql_query("SELECT * FROM signal_events", conn)
        trd = pd.read_sql_query(
            "SELECT code, name, scheme, entry_date, exit_date, "
            "exit_reason, entry_price, exit_price FROM backtest_trades", conn)
        conn.close()
        if len(sig) > 0:
            sig['date'] = pd.to_datetime(sig['date'])
        if len(trd) > 0:
            trd['entry_date'] = pd.to_datetime(trd['entry_date'])
            trd['exit_date'] = pd.to_datetime(trd['exit_date'])
        print(f"标记数据加载完成: E2信号 {len(sig)} 条, 回测交易 {len(trd)} 笔")
        return sig, trd
    except Exception as e:
        print(f"标记数据加载失败: {e}（信号/买卖点标记将不可用）")
        empty = pd.DataFrame(columns=['date', 'code', 'event', 'close',
                                      'share_z_20', 'price_dev_20'])
        empty_trd = pd.DataFrame(columns=['code', 'name', 'scheme',
                                          'entry_date', 'exit_date',
                                          'exit_reason', 'entry_price',
                                          'exit_price'])
        return empty, empty_trd


E2_MARKERS, TRADE_MARKERS = load_markers()

E2_SCHEMES = ('fixed_40', 'ma20_early', 'both')


def _marker_scale_yi(code, date):
    """取该基金某日份额规模（亿份），无记录返回 None"""
    rows = ALL_DATA[(ALL_DATA['基金代码'] == code)
                    & (ALL_DATA['日期'] == pd.Timestamp(date))]
    if len(rows) == 0:
        return None
    return round(float(rows['基金规模(份)'].iloc[0]) / 100_000_000, 2)


def _filter_trades(scheme):
    """按方案筛选交易：both 时全部，否则仅该方案"""
    if scheme == 'both':
        return TRADE_MARKERS
    return TRADE_MARKERS[TRADE_MARKERS['scheme'] == scheme]


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/ranking-app')
def ranking_app():
    """Responsive local-web counterpart of the native mini program."""
    return render_template('ranking_app.html')


@app.route('/api/themes')
def get_themes():
    """返回所有可用的行业主题列表及各主题基金数量"""
    latest = ALL_DATA[ALL_DATA['日期'] == ALL_DATA['日期'].max()]
    themes = []
    for t in ALL_THEMES:
        count = len(latest[latest['行业主题'] == t])
        themes.append({'name': t, 'count': int(count)})
    return jsonify(themes)


@app.route('/api/funds')
def get_funds():
    """返回全部基金清单，按最新规模倒序。支持 ?year= 和 ?theme= 筛选"""
    year_filter = request.args.get('year', '').strip()
    theme_filter = request.args.get('theme', '').strip()
    latest = ALL_DATA[ALL_DATA['日期'] == ALL_DATA['日期'].max()]
    latest = latest.sort_values('基金规模(份)', ascending=False)
    funds = []
    for _, row in latest.iterrows():
        # 发行年份筛选
        issue_year = int(row['发行年份']) if pd.notna(row.get('发行年份')) else 0
        if year_filter and str(issue_year) != year_filter:
            continue
        # 行业主题筛选
        theme = str(row.get('行业主题', ''))
        if pd.isna(row.get('行业主题')):
            theme = ''
        if theme_filter and theme != theme_filter:
            continue
        t = row.get('类型', '')
        if pd.isna(t):
            t = ''
        m = row.get('市场', '')
        if pd.isna(m):
            m = ''
        funds.append({
            'code': str(row['基金代码']),
            'name': row['基金简称'],
            'scale': float(row['基金规模(份)']),
            'scale_yi': round(float(row['基金规模(份)']) / 100_000_000, 2),
            'market': m,
            'type': t,
            'issue_year': issue_year,
            'theme': theme
        })
    return jsonify(funds)


@app.route('/api/scale-movers')
def get_scale_movers():
    """从 etf.db 比较指定日期与前一个全局数据日期的 ETF 份额。"""
    selected_date = request.args.get('date', '').strip()
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', selected_date):
        return jsonify({'error': '日期格式须为 YYYY-MM-DD'}), 400
    try:
        datetime.strptime(selected_date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': '日期无效'}), 400

    with closing(sqlite3.connect(db.DB_PATH, timeout=10)) as conn:
        has_date = conn.execute(
            'SELECT 1 FROM fund_scale WHERE date = ? LIMIT 1', (selected_date,)
        ).fetchone()
        if not has_date:
            return jsonify({'date': selected_date, 'previous_date': None,
                            'gainers': [], 'losers': [], 'message': '所选日期没有份额数据'})

        previous_row = conn.execute(
            'SELECT MAX(date) FROM fund_scale WHERE date < ?', (selected_date,)
        ).fetchone()
        previous_date = previous_row[0]
        if previous_date is None:
            return jsonify({'date': selected_date, 'previous_date': None,
                            'gainers': [], 'losers': [], 'message': '没有更早的数据日期可供比较'})

        rows = conn.execute('''
            SELECT current.code, COALESCE(f.name, current.code),
                   f.issue_year, COALESCE(f.theme, ''),
                   current.shares, previous.shares,
                   current.shares - previous.shares AS change_shares
            FROM fund_scale AS current
            JOIN fund_scale AS previous
              ON previous.code = current.code AND previous.date = ?
            LEFT JOIN funds AS f ON f.code = current.code
            WHERE current.date = ?
        ''', (previous_date, selected_date)).fetchall()

    def as_fund(row):
        code, name, issue_year, theme, shares, previous_shares, change = row
        return {
            'code': code, 'name': name, 'issue_year': issue_year or 0,
            'theme': theme, 'scale_yi': round(shares / 100_000_000, 2),
            'previous_scale_yi': round(previous_shares / 100_000_000, 2),
            'delta_yi': round(change / 100_000_000, 2),
            'direction': 'gain' if change > 0 else 'loss',
        }

    gainers = sorted((row for row in rows if row[6] > 0),
                     key=lambda row: (-row[6], row[0]))[:10]
    losers = sorted((row for row in rows if row[6] < 0),
                    key=lambda row: (row[6], row[0]))[:10]
    return jsonify({'date': selected_date, 'previous_date': previous_date,
                    'gainers': [as_fund(row) for row in gainers],
                    'losers': [as_fund(row) for row in losers], 'message': ''})


@app.route('/api/rankings')
def get_rankings():
    """Return the read-only ETF daily and weekly ranking dashboard payload."""
    selected_date = request.args.get('date', '').strip()
    if selected_date and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', selected_date):
        return jsonify({'error': '日期格式须为 YYYY-MM-DD'}), 400
    if selected_date:
        try:
            datetime.strptime(selected_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': '日期无效'}), 400
    try:
        return jsonify(build_rankings(selected_date or None))
    except RankingNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    except RankingInputError as exc:
        return jsonify({'error': str(exc)}), 422
    except Exception:
        app.logger.exception('龙虎榜计算失败')
        return jsonify({'error': '龙虎榜数据暂不可用，请稍后重试'}), 500


@app.route('/api/trend/<codes>')
def get_trend(codes):
    """返回指定基金的规模趋势数据"""
    code_list = codes.split(',')
    trend = {}
    for code in code_list:
        fund_data = ALL_DATA[ALL_DATA['基金代码'] == code].sort_values('日期')
        if len(fund_data) > 0:
            name = fund_data['基金简称'].iloc[0]
            market = fund_data['市场'].iloc[0] if '市场' in fund_data.columns else ''
            trend[code] = {
                'name': name,
                'market': market,
                'dates': fund_data['日期'].dt.strftime('%Y-%m-%d').tolist(),
                'scales': (fund_data['基金规模(份)'] / 100_000_000).round(2).tolist()
            }
    return jsonify(trend)


@app.route('/api/summary/<codes>')
def get_summary(codes):
    """返回选中基金按日期汇总的份额、金额和有价基金的简单平均收盘价"""
    code_list = codes.split(',')
    # 合并所有选中基金的数据
    pieces = []
    for code in code_list:
        fund_data = ALL_DATA[ALL_DATA['基金代码'] == code][['日期', '基金代码', '基金规模(份)']].copy()
        if len(fund_data) > 0:
            pieces.append(fund_data)
    if not pieces:
        return jsonify({'dates': [], 'scales': [], 'amounts': [], 'average_prices': [],
                        'has_amounts': False, 'has_average_prices': False,
                        'fund_count': 0, 'fund_names': []})

    combined = pd.concat(pieces, ignore_index=True)
    # 按日期汇总规模
    summary = combined.groupby('日期')['基金规模(份)'].sum().reset_index()
    summary = summary.sort_values('日期')

    # 仅用当日有收盘价的基金计算金额及价格；没有价格的日期保留空值。
    amounts = [None] * len(summary)
    average_prices = [None] * len(summary)
    if PRICES is not None:
        price_data = PRICES[PRICES['基金代码'].isin(code_list)][['日期', '基金代码', '收盘价']]
        price_data = price_data.drop_duplicates(subset=['日期', '基金代码'], keep='first')
        merged = combined.merge(price_data, on=['日期', '基金代码'], how='left')
        merged['金额'] = (merged['基金规模(份)'] / 100_000_000) * merged['收盘价']  # 亿份×元 = 亿元
        daily_amounts = merged.groupby('日期')['金额'].sum(min_count=1)
        daily_prices = merged.groupby('日期')['收盘价'].mean()
        for i, date in enumerate(summary['日期']):
            amount = daily_amounts.get(date)
            price = daily_prices.get(date)
            if pd.notna(amount):
                amounts[i] = round(float(amount), 2)
            if pd.notna(price):
                average_prices[i] = round(float(price), 3)

    # 收集基金名称
    names = []
    for code in code_list:
        fd = ALL_DATA[ALL_DATA['基金代码'] == code]
        if len(fd) > 0:
            names.append(fd['基金简称'].iloc[0])

    result = {
        'dates': summary['日期'].dt.strftime('%Y-%m-%d').tolist(),
        'scales': (summary['基金规模(份)'] / 100_000_000).round(2).tolist(),
        'amounts': amounts,
        'average_prices': average_prices,
        'has_amounts': any(amount is not None for amount in amounts),
        'has_average_prices': any(price is not None for price in average_prices),
        'fund_count': len(code_list),
        'fund_names': ' + '.join(names)
    }
    return jsonify(result)


@app.route('/api/total-scale')
def get_total_scale():
    """返回每日 ETF 总规模趋势（查询 v_total_scale 视图）"""
    # Flask 多线程：每请求独立连接，WAL 模式下更新时也可安全读取
    conn = db.connect()
    df = pd.read_sql_query("SELECT * FROM v_total_scale", conn)
    conn.close()
    if len(df) == 0:
        return jsonify({'dates': [], 'scales': [], 'fund_counts': [], 'error': '数据库为空'})

    # 总金额可能为 NULL（无价格数据时）
    amounts = [None if pd.isna(x) else x for x in df['total_amount_yi'].tolist()]
    priced_counts = [None if pd.isna(x) else int(x) for x in df['priced_count'].tolist()]
    return jsonify({
        'dates': df['date'].tolist(),
        'scales': df['total_shares_yi'].tolist(),
        'amounts': amounts,
        'fund_counts': df['fund_count'].tolist(),
        'priced_counts': priced_counts,
        'latest_date': df['date'].iloc[-1],
        'latest_scale': float(df['total_shares_yi'].iloc[-1]),
        'latest_amount': float(df['total_amount_yi'].iloc[-1]) if pd.notna(df['total_amount_yi'].iloc[-1]) else None,
        'latest_count': int(df['fund_count'].iloc[-1]),
        'total_days': len(df)
    })


@app.route('/api/price-trend/<codes>')
def get_price_trend(codes):
    """返回指定基金的规模+价格趋势数据（支持双Y轴图表）"""
    code_list = codes.split(',')
    trend = {}
    for code in code_list:
        fund_data = ALL_DATA[ALL_DATA['基金代码'] == code].sort_values('日期')
        if len(fund_data) == 0:
            continue
        name = fund_data['基金简称'].iloc[0]
        market = fund_data['市场'].iloc[0] if '市场' in fund_data.columns else ''

        dates = fund_data['日期'].dt.strftime('%Y-%m-%d').tolist()
        scales = (fund_data['基金规模(份)'] / 100_000_000).round(2).tolist()

        result = {
            'name': name,
            'market': market,
            'dates': dates,
            'scales': scales,
            'prices': [],
            'has_prices': False
        }

        # 合并价格数据
        if PRICES is not None:
            price_data = PRICES[PRICES['基金代码'] == code].sort_values('日期')
            # 防御性去重（防止CSV中存在重复的基金代码+日期组合）
            price_data = price_data.drop_duplicates(subset=['日期'], keep='first')
            if len(price_data) > 0:
                # left join：以规模数据日期为基准，对齐价格
                merged = fund_data[['日期']].merge(
                    price_data[['日期', '收盘价']], on='日期', how='left'
                )
                result['prices'] = [
                    round(float(x), 3) if pd.notna(x) else None
                    for x in merged['收盘价'].tolist()
                ]
                result['has_prices'] = any(p is not None for p in result['prices'])

        trend[code] = result
    return jsonify(trend)


@app.route('/api/markers/<codes>')
def get_markers(codes):
    """返回选中基金的 E2 信号 + 买卖点标记数据

    信号来自 signal_events（仅 E2），买卖点来自 backtest_trades。
    ?scheme=fixed_40|ma20_early|both（默认 fixed_40，即方案1）。
    非核心基金（无记录）返回空数组，不报错。
    """
    scheme = request.args.get('scheme', 'fixed_40').strip()
    if scheme not in E2_SCHEMES:
        scheme = 'fixed_40'
    code_list = codes.split(',')
    trades = _filter_trades(scheme)
    markers = {}
    for code in code_list:
        sigs = E2_MARKERS[E2_MARKERS['code'] == code]\
            .sort_values('date') if len(E2_MARKERS) > 0 else []
        trd_code = trades[trades['code'] == code]\
            .sort_values('entry_date') if len(trades) > 0 else []
        signals = []
        buys = []
        sells = []
        if len(sigs) > 0:
            for _, r in sigs.iterrows():
                d = r['date'].strftime('%Y-%m-%d')
                signals.append({
                    'date': d,
                    'event': str(r.get('event', '') if pd.notna(r.get('event')) else ''),
                    'close': round(float(r['close']), 3) if pd.notna(r.get('close')) else None,
                    'share_z_20': round(float(r['share_z_20']), 2) if pd.notna(r.get('share_z_20')) else None,
                    'price_dev_20': round(float(r['price_dev_20']), 4) if pd.notna(r.get('price_dev_20')) else None,
                    'scale_yi': _marker_scale_yi(code, r['date']),
                })
        if len(trd_code) > 0:
            seen_buy = set()
            for _, r in trd_code.iterrows():
                entry_d = r['entry_date'].strftime('%Y-%m-%d')
                exit_d = r['exit_date'].strftime('%Y-%m-%d')
                s = str(r['scheme'])
                if entry_d not in seen_buy:
                    seen_buy.add(entry_d)
                    buys.append({
                        'date': entry_d,
                        'price': round(float(r['entry_price']), 3) if pd.notna(r.get('entry_price')) else None,
                        'scale_yi': _marker_scale_yi(code, r['entry_date']),
                        'scheme': s,
                    })
                sells.append({
                    'date': exit_d,
                    'price': round(float(r['exit_price']), 3) if pd.notna(r.get('exit_price')) else None,
                    'scale_yi': _marker_scale_yi(code, r['exit_date']),
                    'scheme': s,
                    'exit_reason': str(r['exit_reason']),
                })
        markers[code] = {
            'name': '', 'scheme': scheme,
            'signals': signals, 'buys': buys, 'sells': sells,
        }
        # 补基金简称
        fd = ALL_DATA[ALL_DATA['基金代码'] == code]
        if len(fd) > 0:
            markers[code]['name'] = fd['基金简称'].iloc[0]
    return jsonify(markers)


if __name__ == '__main__':
    port = int(os.getenv("ETF_WEB_PORT", "5010"))
    print(f"\n启动服务: http://127.0.0.1:{port}")
    app.run(debug=False, host='127.0.0.1', port=port)
