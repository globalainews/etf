# -*- coding: utf-8 -*-
"""多主题动量与资金面结合 ETF 轮动策略 — Step 2: 信号合成与多主题打分引擎

读取 Step 1 的因子宽表 (multi_factor_builder.compute_all_factors)，
按交易日逐日执行：

1. 动态截面清洗    — 剔除当日未上市 / 因子为 NaN 的标的
2. 因子标准化打分  — 横截面分位打分 + 信号加分 + 风险惩罚
3. 主题去重与权重  — 组内最多选 1 只，进攻全哑火时转向防守组 512890

打分公式：
    S_mom    = 0.6 × Rank_pct(RS20) + 0.4 × Rank_pct(RS60)      ∈ (0, 1)
    S_signal = 0.15 × [vol_impulse > 1.3] + 0.20 × [divergence_20 == 1]
    P_risk   = 0.10 × Rank_pct(vol20_annual) + 0.10 × clamp(|dd60| / 60, 0, 1)
    Score    = S_mom + S_signal - P_risk                          ∈ (-0.2, 1.35)

用法：
    from src.strategy.signal_generator import generate_signals, demo
    hist = generate_signals(start_date='2019-01-01')
    demo()   # 打印关键验证日期（2020-07 / 2024-09 / 2026 最新）
"""

import sys
import io
import sqlite3
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# 保证以脚本方式运行时也能导入同包模块（sys.path[0] 是 src/strategy 而非项目根）
ROOT = str(Path(__file__).resolve().parent.parent.parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.strategy.multi_factor_builder import compute_all_factors, DB_PATH

# ──────────────────────────────────────────────
# 主题分组（供组内去重与防守判断）
# ──────────────────────────────────────────────
THEME_GROUPS = {
    '证券':   ['512000', '512880'],
    '恒科':   ['513180', '513130'],
    '医药':   ['159992', '515120', '513120'],
    '半导体': ['159516'],
    '防守':   ['512890'],                      # 红利低波
}
OFFENSE_GROUPS = ['证券', '恒科', '医药', '半导体']   # 进攻组（不含防守）

# ├── 打分参数 ────────────────────────────────
W_MOM_RS20 = 0.6        # RS20 权重
W_MOM_RS60 = 0.4        # RS60 权重
BONUS_VOL  = 0.15       # 量能爆发加分 (vol_impulse > 1.3)
BONUS_DIV  = 0.20       # 背离信号加分 (divergence_20 == 1)
PPEN_VOL   = 0.10       # 波动率惩罚系数
PPEN_DD    = 0.10       # 回撤惩罚系数
DD_CAP     = 60.0       # 回撤惩罚封顶阈值（%）

VOL_IMPULSE_TH = 1.3    # 量能爆发阈值
TOP_N         = 3       # 正常行情下最多选出的标的数
DEFENSE_CODE  = '512890'  # 防守组标的（红利低波）

# Step 4 新增参数
SCORE_BUFFER      = 0.15   # 得分差调仓门槛：新标的分 > 持仓最低分 + buffer 才替换
MIN_SCORE_THRESH  = 0.25   # 单标的组硬风险门槛：组内最高分低于此值则跳过，转防守

# ──────────────────────────────────────────────
# 数据加载
# ──────────────────────────────────────────────

def load_factor_table(start_date: str = '2019-01-01') -> pd.DataFrame:
    """加载 Step 1 因子宽表（含基金名称映射）。"""
    return compute_all_factors(start_date=start_date)


def valid_cross_section(df_day: pd.DataFrame) -> pd.DataFrame:
    """动态截面清洗：保留当日指标齐备（动量、量能、风险均非 NaN）的标的。

    参数:
        df_day: 单日切片（含 close/volume/shares 及全部 factor_ 列）

    返回:
        清洗后的 DataFrame，行 = 当日有效标的
    """
    needed = ['factor_mom_RS20', 'factor_mom_RS60',
              'factor_vol_impulse', 'vol20_annual', 'dd60']
    out = df_day.copy()
    for col in needed:
        out = out[out[col].notna()]
    return out


# ──────────────────────────────────────────────
# 因子标准化与综合打分
# ──────────────────────────────────────────────

def compute_daily_scores(df_day: pd.DataFrame) -> pd.DataFrame:
    """对单日截面对所有有效标的打分。

    参数:
        df_day: 单日切片（valid_cross_section 已清洗）

    返回:
        加了 s_mom/s_signal/p_risk/score 列的单日 DataFrame
    """
    df = df_day.copy()

    # 1) 横截面分位（至少 2 只才成截面；pct rank 默认排位 1~N，用 pct=True 归一到 (0,1]）
    if len(df) >= 2:
        r_rs20 = df['factor_mom_RS20'].rank(pct=True)
        r_rs60 = df['factor_mom_RS60'].rank(pct=True)
        r_vol  = df['vol20_annual'].rank(pct=True)
    else:
        r_rs20 = r_rs60 = r_vol = pd.Series(0.5, index=df.index)

    df['rank_rs20'] = r_rs20
    df['rank_rs60'] = r_rs60

    # 2) 动量分：0.6 × Rank(RS20) + 0.4 × Rank(RS60)
    df['s_mom'] = W_MOM_RS20 * r_rs20 + W_MOM_RS60 * r_rs60

    # 3) 信号加分
    df['s_signal'] = (
        (df['factor_vol_impulse'] > VOL_IMPULSE_TH).astype(float) * BONUS_VOL
        + (df['divergence_20'] == 1).astype(float) * BONUS_DIV
    )

    # 4) 风险惩罚（适度）：波动率取截面分位（高波高罚），回撤按绝对深度
    risk_vol = 0.10 * r_vol
    risk_dd  = 0.10 * np.clip(df['dd60'].abs() / DD_CAP, 0, 1)
    df['p_risk'] = risk_vol + risk_dd

    # 5) 最终得分
    df['score'] = df['s_mom'] + df['s_signal'] - df['p_risk']

    return df


# ──────────────────────────────────────────────
# 主题去重与持仓权重分配
# ──────────────────────────────────────────────

def detect_regime(
    df_scored: pd.DataFrame,
    bench_ma60: float = None,
    bench_close: float = None,
) -> str:
    """判断当日市场状态。

    进攻组（非防守）全部跑输大盘（RS20<0 且 RS60<0）→ 进入防守态。
    Step 4 增强：引入基准指数（000300）价格与 MA60 均线。若大盘价格低于 MA60
    且进攻组整体 RS60 < 0，强制 risk_off。

    返回: 'risk_off' | 'mixed' | 'normal'
      risk_off: 进攻组全部 R20<0 且 RS<0（绝对+相对双弱）→ 100% 防守
      mixed   : 进攻组 RS<0 但存在 R20>0（相对弱、绝对尚可）→ 50% 防守
      normal  : 有进攻组跑赢大盘 → 正常选股
    """
    offense = df_scored[df_scored['group'] != '防守']
    if len(offense) == 0:
        return 'risk_off'

    # 组级动量 = 组内最高分
    g = offense.groupby('group').agg(
        max_rs20=('factor_mom_RS20', 'max'),
        max_rs60=('factor_mom_RS60', 'max'),
        max_r20=('factor_mom_R20', 'max'),
    )
    rs_all_neg = (g['max_rs20'] < 0).all() and (g['max_rs60'] < 0).all()

    # Step 4 宏观滤网：大盘价格 < MA60 且进攻组 RS60 < 0 → 强制 risk_off
    if bench_ma60 is not None and bench_close is not None:
        if bench_close < bench_ma60 and g['max_rs60'].all() < 0:
            return 'risk_off'

    if not rs_all_neg:
        return 'normal'

    # 全部跑输大盘：再看绝对动量，判 100% / 50% 防守
    abs_all_neg = (g['max_r20'] < 0).all()
    return 'risk_off' if abs_all_neg else 'mixed'


def select_portfolio(
    df_scored: pd.DataFrame,
    regime: str,
    current_hold: dict = None,
    score_buffer: float = 0.0,
    min_score_threshold: float = None,
    linear_weight: bool = False,
) -> pd.DataFrame:
    """按主题组做去重选股并分配权重。

    参数:
        df_scored: 单日已打分切片
        regime: detect_regime 的输出
        current_hold: 当前持仓 {code: weight}（Step 4 新增，用于 deadband 检查）
        score_buffer: 得分差门槛（Step 4 新增）
        min_score_threshold: 单标的组硬风险门槛（Step 4 新增）
        linear_weight: 是否按得分线性加权（Step 4 新增）

    返回:
        持仓 DataFrame: [etf_code, weight, score, group, rank_in_group]
    """
    df = df_scored.copy()
    df = df.sort_values('score', ascending=False).reset_index(drop=True)

    # 构建 code → group 映射
    code2group = {}
    for grp, codes in THEME_GROUPS.items():
        for c in codes:
            code2group[c] = grp

    picks = []

    if regime == 'risk_off':
        # 100% 防守
        d = df[df['etf_code'] == DEFENSE_CODE]
        if len(d) == 0:
            return pd.DataFrame(columns=['etf_code', 'weight', 'score', 'group'])
        picks.append({'etf_code': DEFENSE_CODE, 'weight': 1.0,
                      'score': float(d.iloc[0]['score']), 'group': '防守'})
    elif regime == 'mixed':
        # 50% 防守 + 50% 给进攻组里得分最高者
        d = df[df['etf_code'] == DEFENSE_CODE]
        if len(d) > 0:
            picks.append({'etf_code': DEFENSE_CODE, 'weight': 0.5,
                          'score': float(d.iloc[0]['score']), 'group': '防守'})
        offense_top = df[df['group'] != '防守'].head(1)
        if len(offense_top) > 0:
            picks.append({'etf_code': offense_top.iloc[0]['etf_code'], 'weight': 0.5,
                          'score': float(offense_top.iloc[0]['score']),
                          'group': offense_top.iloc[0]['group']})
        else:
            picks[0]['weight'] = 1.0   # 无进攻标的时全防守
    else:
        # normal: 只从进攻组选股（防守仅在进攻全哑火时启用）
        df_offense = df[df['group'] != '防守']
        chosen_groups = set()

        # 当前持仓分组集合
        held_groups = set()
        if current_hold:
            for code in current_hold:
                grp = code2group.get(code)
                if grp and grp != '防守':
                    held_groups.add(grp)

        # 当前持仓中最低分
        held_min_score = None
        if current_hold and len(current_hold) > 0:
            held_scores = []
            for code in current_hold:
                match = df_scored[df_scored['etf_code'] == code]
                if len(match) > 0:
                    held_scores.append(match.iloc[0]['score'])
            if held_scores:
                held_min_score = min(held_scores)

        for _, row in df_offense.iterrows():
            if len(picks) >= TOP_N:
                break
            grp = row['group']
            if grp in chosen_groups:
                continue

            # Step 4: 单标的组硬风险门槛
            if min_score_threshold is not None and row['score'] < min_score_threshold:
                continue

            # Step 4: Deadband 检查
            if (score_buffer > 0 and grp in held_groups
                    and held_min_score is not None):
                if row['score'] < held_min_score + score_buffer:
                    continue

            chosen_groups.add(grp)
            picks.append({'etf_code': row['etf_code'], 'weight': 0.0,
                          'score': float(row['score']), 'group': grp})

        if len(picks) == 0:
            # 进攻组全部不可用（极端预热期 / 全被风险门槛过滤）→ 兜底防守
            d = df_scored[df_scored['etf_code'] == DEFENSE_CODE]
            if len(d) > 0:
                return pd.DataFrame([{'etf_code': DEFENSE_CODE, 'weight': 1.0,
                                      'score': float(d.iloc[0]['score']),
                                      'group': '防守'}])
            return pd.DataFrame(columns=['etf_code', 'weight', 'score', 'group'])

        # 权重分配：等权 或 线性加权
        if linear_weight:
            scores = np.array([p['score'] for p in picks])
            pos_mask = scores > 0
            if (pos_mask).sum() > 0:
                pos_scores = scores[pos_mask]
                total_score = pos_scores.sum()
                for i, p in enumerate(picks):
                    if pos_mask[i] and total_score > 0:
                        p['weight'] = pos_scores[i] / total_score
                    else:
                        p['weight'] = 0.0
            else:
                w = 1.0 / len(picks)
                for p in picks:
                    p['weight'] = w
        else:
            # 等权分配
            w = 1.0 / len(picks)
            for p in picks:
                p['weight'] = w

    return pd.DataFrame(picks)


# ──────────────────────────────────────────────
# 全历史信号合成
# ──────────────────────────────────────────────

def generate_signals(
    start_date: str = '2019-01-01',
    bench_ma60_map: dict = None,
    bench_close_map: dict = None,
    score_buffer: float = 0.0,
    min_score_threshold: float = None,
    linear_weight: bool = False,
) -> pd.DataFrame:
    """遍历全部交易日，输出每日选股与权重分配。

    Step 4 新增参数:
        bench_ma60_map: {date: MA60值} 大盘 60 日均线
        bench_close_map: {date: close} 大盘收盘价
        score_buffer: 得分差门槛
        min_score_threshold: 单标的组硬风险门槛
        linear_weight: 是否线性加权
    """
    factors = load_factor_table(start_date)

    # 标注主题分组
    code2group_local = {}
    for grp, codes in THEME_GROUPS.items():
        for c in codes:
            code2group_local[c] = grp
    factors['group'] = factors['etf_code'].map(code2group_local)

    date_idx = pd.CategoricalDtype(categories=sorted(factors['date'].unique()), ordered=True)
    history = []

    for d, day in factors.groupby('date', sort=True):
        sect = valid_cross_section(day)
        if len(sect) == 0:
            continue
        scored = compute_daily_scores(sect)
        # Step 4: 传入宏观滤网
        regime = detect_regime(scored,
                             bench_ma60=bench_ma60_map.get(d) if bench_ma60_map else None,
                             bench_close=bench_close_map.get(d) if bench_close_map else None)
        picks = select_portfolio(scored, regime,
                                score_buffer=score_buffer,
                                min_score_threshold=min_score_threshold,
                                linear_weight=linear_weight)
        if len(picks) == 0:
            continue
        picks = picks.copy()
        picks['date'] = d
        picks['regime'] = regime
        # 附上因子明细供分析（按 code 关联）
        detail = scored.set_index('etf_code')
        for col in ['factor_mom_R20', 'factor_mom_RS20', 'factor_mom_RS60',
                    'factor_vol_impulse', 'divergence_20', 'vol20_annual', 'dd60',
                    's_mom', 's_signal', 'p_risk', 'rank_rs20', 'rank_rs60']:
            picks[col] = picks['etf_code'].map(detail[col])
        history.append(picks)

    result = pd.concat(history, ignore_index=True)

    # 基金名称
    try:
        conn = sqlite3.connect(DB_PATH)
        name_map = dict(conn.execute("SELECT code, name FROM funds").fetchall())
        conn.close()
        result['etf_name'] = result['etf_code'].map(name_map)
    except Exception:
        result['etf_name'] = ''

    result = result[['date', 'etf_code', 'etf_name', 'group', 'weight', 'score',
                     's_mom', 's_signal', 'p_risk', 'factor_mom_R20',
                     'factor_mom_RS20', 'factor_mom_RS60', 'factor_vol_impulse',
                     'divergence_20', 'vol20_annual', 'dd60',
                     'rank_rs20', 'rank_rs60', 'regime']]
    return result


# ──────────────────────────────────────────────
# 权重矩阵与验证输出
# ──────────────────────────────────────────────

def weight_matrix(history: pd.DataFrame, dates: list) -> pd.DataFrame:
    """把指定日期集合的持仓转成 date × etf_code 权重矩阵。"""
    sub = history[history['date'].isin([pd.Timestamp(d) for d in dates])]
    mat = sub.pivot_table(index='date', columns='etf_code', values='weight',
                          aggfunc='sum', fill_value=0)
    mat['_regime'] = sub.groupby('date')['regime'].first()
    mat = mat.rename(columns={'_regime': '__regime__'})
    return mat


def demo():
    """Step 2 验证：打印 2020-07 / 2024-09 / 2026 最新交易日的选股与权重。"""
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

    print("=" * 78)
    print("多主题轮动策略 — Step 2 信号合成与打分引擎 验证")
    print("=" * 78)

    print("\n[1] 生成全历史信号...")
    hist = generate_signals(start_date='2019-01-01')
    print(f"  信号行数: {len(hist)}")
    print(f"  日期范围: {hist['date'].min().date()} ~ {hist['date'].max().date()}")
    print(f"  涉及标的: {sorted(hist['etf_code'].unique())}")

    # 市场状态分布
    print("\n[2] 市场状态(regime)统计:")
    print(hist.groupby('regime')['date'].nunique().to_string())

    # 持仓次数统计
    print("\n[3] 各标的被选中次数:")
    cnt = hist.groupby(['etf_code', 'etf_name'])['date'].nunique().sort_values(ascending=False)
    print(cnt.to_string())

    # 关键验证日期
    key_dates = [
        # 2020-07 证券爆发
        ('2020-07-06', '证券爆发期 前'),
        ('2020-07-10', '证券爆发期'),
        ('2020-07-13', '证券爆发期尾'),
        # 2024-09 大反弹
        ('2024-09-20', '反弹前(磨底)'),
        ('2024-09-24', '924 行情启动日'),
        ('2024-09-27', '924 行情 反弹中'),
        ('2024-09-30', '924 行情 放量冲刺'),
        ('2024-10-08', '节后首日(冲高)'),
        # 2026 最新
        (str(hist['date'].max().date()), '最新交易日'),
    ]

    print("\n[4] 关键验证日期持仓明细")
    factor_df = load_factor_table('2019-01-01')
    detail_cols = ['etf_code', 'etf_name', 'close', \
                   'factor_mom_RS20', 'factor_mom_RS60', 'factor_vol_impulse', \
                   'divergence_20', 'vol20_annual', 'dd60', 'score', 'group']
    for d, label in key_dates:
        ts = pd.Timestamp(d)
        sub = hist[hist['date'] == ts]
        if len(sub) == 0:
            # 无持仓时展示当日完整有效截面（说明为何空仓）
            day_factors = factor_df[factor_df['date'] == ts]
            sect = valid_cross_section(day_factors)
            print(f"\n  --- {label} ({d}) ---  [空仓 / 无持仓记录]")
            print(f"  当日有效截面 ({len(sect)} 只): " +
                  ', '.join(f"{r.etf_code}({r.etf_name}, RS20={r.factor_mom_RS20:.2f})" for r in sect.itertuples()))
            continue
        picks_cols = ['etf_code', 'etf_name', 'group', 'weight', 'score', 'regime',
                      'factor_mom_RS20', 'factor_mom_RS60', 'factor_vol_impulse',
                      'divergence_20', 'vol20_annual', 'dd60']
        print(f"\n  --- {label} ({d}) ---  [{sub.iloc[0]['regime']}] 持仓 {len(sub)} 只")
        # 背景：当日全部有效截面，标出选中者
        day_factors = factor_df[factor_df['date'] == ts]
        sect = valid_cross_section(day_factors)
        if len(sect) > 0 and len(sect) > len(sub):
            picked = set(sub['etf_code'])
            bg = [f"{r.etf_code}({r.etf_name},RS20={r.factor_mom_RS20:.2f})"
                  + ("★" if r.etf_code in picked else "")
                  for r in sect.itertuples()]
            print(f"  当日有效截面: {' | '.join(bg)}")
        print(sub[picks_cols].to_string(
            float_format=lambda x: f'{x:8.3f}' if isinstance(x, float) else str(x),
            index=False))

    # 权重分配矩阵
    print("\n[5] 权重分配矩阵 (最近一个月)")
    dates31 = sorted(hist[hist['date'] >= hist['date'].max() - pd.Timedelta(days=31)]
                     ['date'].unique())
    mat = weight_matrix(hist, [str(x.date()) for x in dates31])
    print(mat.to_string(float_format=lambda x: f'{x:.2f}'))

    print("\n" + "=" * 78)
    print("Step 2 验证完成。")
    print("=" * 78)


if __name__ == '__main__':
    demo()