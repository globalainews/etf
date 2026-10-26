"""Audited split-adjusted shares and comparable three-ETF research (no orders).

Reuses the frozen Wind close/open snapshot; any extra history is research-only.
The adjusted open is a total-return proxy, NOT a directly tradable price.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from research.three_etf_validation import CODES, ROOT, BASE_COST, candidate_weights

SOURCE = ROOT / "output" / "three_etf_validation" / "wind_adjusted_ohlc.csv"
OUT = ROOT / "output" / "three_etf_validation_v2"
START = "2022-03-08"  # 180-day warm-up AND first weekly signal have passed
SPLITS = {"512890": ("2021-10-22", 2.0), "512480": ("2026-07-02", 2.0)}


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    raw = pd.read_csv(SOURCE, dtype={"code": str})
    assert set(CODES) == set(raw.code.unique())
    dates = pd.Index(sorted(raw.date.unique()))
    op = raw.pivot(index="date", columns="code", values="open").reindex(dates, columns=CODES)
    cl = raw.pivot(index="date", columns="code", values="close").reindex(dates, columns=CODES)
    vol = raw.pivot(index="date", columns="code", values="volume").reindex(dates, columns=CODES)
    conn = sqlite3.connect(f"file:{ROOT / 'etf.db'}?mode=ro", uri=True)
    shares = pd.read_sql_query(
        "SELECT date, code, shares FROM fund_scale WHERE code IN ('512890','513130','512480')", conn
    ).pivot(index="date", columns="code", values="shares").reindex(dates, columns=CODES)
    raw_close = pd.read_sql_query(
        "SELECT date, code, close FROM fund_price WHERE code IN ('512890','513130','512480')", conn
    ).pivot(index="date", columns="code", values="close").reindex(dates, columns=CODES)
    conn.close()
    # The final price session can precede the next fund-scale publication.
    if shares.iloc[-1].isna().any() and shares.iloc[:-1].notna().all().all():
        shares.iloc[-1] = shares.iloc[-2]
    if op.isna().any().any() or cl.isna().any().any() or shares.isna().any().any():
        raise ValueError("Price/scale coverage incomplete; do not forward fill across gaps")
    audits = []
    for code in CODES:
        ratio = shares[code] / shares[code].shift(1)
        candidates = ratio[(ratio > 1.6) | (ratio < 0.625)]
        expected = [SPLITS[code][0]] if code in SPLITS else []
        if list(candidates.index) != expected:
            raise ValueError(f"Unreviewed share discontinuity {code}: {candidates.to_dict()}")
        if code not in SPLITS:
            continue
        date, factor = SPLITS[code]
        pos = dates.get_loc(date)
        if abs(ratio.loc[date] / factor - 1) > .03:
            raise ValueError(f"Split ratio not confirmed: {code} {date}")
        # Source fund_scale can record new units 1-2 sessions before the raw
        # traded quote changes. Reconciliation uses the observed next quotes.
        price_ratios = (raw_close[code] / raw_close[code].shift(1)).iloc[pos:pos + 3]
        if not any(abs(x * factor - 1) < .15 for x in price_ratios.dropna()):
            raise ValueError(f"Raw price split confirmation missing: {code} {date}")
        shares.loc[dates < date, code] *= factor  # current-share units, same as front-adjusted prices
        audits.append({"code": code, "share_date": date, "factor": factor,
                       "raw_share_ratio": float(ratio.loc[date]),
                       "price_confirmation_date": price_ratios.sub(1 / factor).abs().idxmin(),
                       "pre_adjusted_shares": float(shares.loc[dates[pos - 1], code]),
                       "post_adjusted_shares": float(shares.loc[date, code])})
    pd.DataFrame(audits).to_csv(OUT / "split_audit.csv", index=False)
    # Day-of-event and 20 lookback sessions must not be treated as creations;
    # publication lag is added after this mask.
    for code, (date, _) in SPLITS.items():
        pos = dates.get_loc(date)
        shares.loc[dates[pos:pos + 23], code] = np.nan
    # Shares are available only after 2 sessions. ffill must not undo blackout.
    return op, cl, vol, shares.shift(2)


def simulate(op: pd.DataFrame, vol: pd.DataFrame, target_close: pd.DataFrame,
             cost: float = BASE_COST, lag: int = 1) -> pd.DataFrame:
    """Adjusted-open weight proxy; no use of today's completed volume to fill."""
    target = target_close.shift(lag)
    tradable = (vol.shift(1).rolling(20, min_periods=5).mean() > 0) & op.gt(0)
    holdings = pd.Series(0., index=CODES)
    nav = 1.
    rows = []
    for i, date in enumerate(op.index[:-1]):
        desired = holdings.copy()
        if target.loc[date].notna().all():
            desired = target.loc[date].copy()
            desired[~tradable.loc[date]] = holdings[~tradable.loc[date]]
            if desired.sum() > 1 + 1e-10 or desired.min() < -1e-10:
                raise ValueError("Invalid target cash/holdings")
        turnover = float((desired - holdings).abs().sum())
        fee = cost * turnover
        change = op.iloc[i + 1] / op.iloc[i] - 1
        if change[desired > 0].isna().any():
            raise ValueError(f"Held quote missing after {date}")
        gross = float((desired * change.fillna(0)).sum())
        nav *= (1 - fee) * (1 + gross)
        holdings = desired * (1 + change.fillna(0)) / (1 + gross)
        rows.append({"date": op.index[i + 1], "nav": nav, "turnover": turnover,
                     "fee": fee, **{f"w_{c}": desired[c] for c in CODES},
                     **{f"gross_contrib_{c}": desired[c] * change[c] for c in CODES}})
    result = pd.DataFrame(rows).set_index("date")
    result["return"] = result.nav.pct_change().fillna(result.nav.iloc[0] - 1)
    return result


def metrics(frame: pd.DataFrame, start: str, end: str) -> dict:
    sample = frame.loc[start:end]
    if sample.empty:
        return {}
    prior = frame.nav.shift(1).loc[sample.index[0]]
    prior = 1. if pd.isna(prior) else float(prior)
    path = pd.concat([pd.Series([prior]), sample.nav.reset_index(drop=True)], ignore_index=True)
    returns = path.pct_change().dropna()
    vol = returns.std()
    return {"total": path.iloc[-1] / prior - 1,
            "cagr": (path.iloc[-1] / prior) ** (252 / len(sample)) - 1,
            "max_dd": (path / path.cummax() - 1).min(),
            "sharpe": returns.mean() / vol * np.sqrt(252) if vol > 0 else 0.,
            "trading_days": int((sample.turnover > 1e-8).sum()),
            "turnover": sample.turnover.sum(), "fee_sum": sample.fee.sum(),
            "mean_exposure": sample[[f"w_{c}" for c in CODES]].sum(axis=1).mean(),
            "days": len(sample)}


def schedule(dates: pd.Index, frequency: str) -> pd.Index:
    periods = pd.to_datetime(dates).to_period("W-FRI" if frequency == "weekly" else "M")
    return pd.Index(pd.Series(dates, index=dates).groupby(periods).last())


def fixed_target(dates: pd.Index, weights: list[float], frequency: str) -> pd.DataFrame:
    result = pd.DataFrame(np.nan, index=dates, columns=CODES)
    result.loc[schedule(dates, frequency)] = weights
    return result


def ex_ante_vol_cap(weights: pd.Series, history: pd.DataFrame, target: float = .10) -> pd.Series:
    """Apply a no-leverage portfolio volatility cap using only past closes."""
    cov = history.pct_change().iloc[-60:].cov().reindex(index=CODES, columns=CODES)
    if cov.isna().any().any():
        return weights * 0  # avoid assigning risk without a full estimate
    sigma = float(np.sqrt(max(0, 252 * weights.to_numpy() @ cov.to_numpy() @ weights.to_numpy())))
    return weights * min(1., target / sigma) if sigma > 0 else weights


def volatility_baseline(cl: pd.DataFrame) -> pd.DataFrame:
    targets = pd.DataFrame(np.nan, index=cl.index, columns=CODES)
    for day in schedule(cl.index, "weekly"):
        targets.loc[day] = ex_ante_vol_cap(pd.Series([.4, .2, .2], index=CODES), cl.loc[:day])
    return targets


def new_target(cl: pd.DataFrame, name: str, window: int) -> pd.DataFrame:
    """Predeclared ablation: fixed, trend-only, risk-only, both."""
    targets = pd.DataFrame(np.nan, index=cl.index, columns=CODES)
    ma = cl.rolling(window, min_periods=window).mean()
    volatility = cl.pct_change().rolling(60, min_periods=60).std()
    for day in schedule(cl.index, "weekly"):
        baseline = pd.Series([.4, .2, .2], index=CODES)
        if name in ("trend", "both"):
            baseline = baseline.where(cl.loc[day] > ma.loc[day], 0.)
        if name in ("risk", "both"):
            # Inverse-vol allocation within the same 80% budget/caps.
            inv = (1 / volatility.loc[day]).replace([np.inf, -np.inf], np.nan).fillna(0)
            if name == "both":
                inv = inv.where(baseline > 0, 0)
            if inv.sum() > 0:
                baseline = (.8 * inv / inv.sum()).clip(upper=pd.Series([.4, .2, .2], index=CODES))
                if name == "both":
                    baseline = baseline.where(cl.loc[day] > ma.loc[day], 0.)
            else:
                baseline[:] = 0
            baseline = ex_ante_vol_cap(baseline, cl.loc[:day])
        targets.loc[day] = baseline
    return targets


def evaluate(curves: dict[str, pd.DataFrame]) -> None:
    periods = [("full", START, "2026-09-15"),
               ("development", START, "2023-12-31"),
               ("validation", "2024-01-01", "2024-12-31"),
               ("historical_reviewed", "2025-01-01", "2026-09-15")]
    rows = [{"strategy": name, "period": label, **metrics(curve, a, b)}
            for name, curve in curves.items() for label, a, b in periods]
    pd.DataFrame(rows).to_csv(OUT / "results.csv", index=False)
    annual = [{"strategy": name, "year": year, **metrics(curve, max(START, f"{year}-01-01"), f"{year}-12-31")}
              for name, curve in curves.items() for year in range(2022, 2027)]
    pd.DataFrame(annual).to_csv(OUT / "annual.csv", index=False)
    for name, curve in curves.items():
        curve.to_csv(OUT / f"nav_{name}.csv")
    # The user has seen the historical data. These rolling windows are
    # diagnostic pseudo-OOS, not a new blind holdout.
    start = pd.Timestamp(START)
    end = pd.Timestamp("2026-09-15")
    folds = []
    k = 0
    while True:
        train_start = start + pd.DateOffset(months=6 * k)
        train_end = train_start + pd.DateOffset(months=18) - pd.Timedelta(days=1)
        validation_end = train_end + pd.DateOffset(months=6)
        test_end = validation_end + pd.DateOffset(months=6)
        if test_end > end:
            break
        va = (train_end + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        te = (validation_end + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        ve, ee = validation_end.strftime("%Y-%m-%d"), test_end.strftime("%Y-%m-%d")
        # Choose two using the earlier train period, one of those using the
        # subsequent validation. Test boundaries never influence selection.
        names = [x for x in curves if x.startswith("candidate_")]
        ts = train_start.strftime("%Y-%m-%d")
        shortlist = sorted(names, key=lambda n: metrics(curves[n], ts, train_end.strftime("%Y-%m-%d"))["total"], reverse=True)[:2]
        winner = max(shortlist, key=lambda n: metrics(curves[n], va, ve)["total"])
        benchmark = "fixed_40_20_20"
        folds.append({"fold": k, "train_start": ts, "train_end": train_end.strftime("%Y-%m-%d"),
                      "validation_start": va, "validation_end": ve,
                      "test_start": te, "test_end": ee, "selected": winner,
                      "train_shortlist": "|".join(shortlist),
                      "validation_return": metrics(curves[winner], va, ve)["total"],
                      "test_return": metrics(curves[winner], te, ee)["total"],
                      "benchmark_return": metrics(curves[benchmark], te, ee)["total"]})
        k += 1
    pd.DataFrame(folds).to_csv(OUT / "rolling_review.csv", index=False)


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    op, cl, vol, shares = load_data()
    curves = {}
    for name, w in (("fixed_40_20_20", [.4, .2, .2]),
                    ("equal_monthly", [1 / 3] * 3),
                    ("fixed_cash25", [.375, .1875, .1875]),
                    ("fixed_cash60_equal", [.4 / 3] * 3),
                    ("fixed_cash60_dividend", [.4, 0, 0])):
        curves[name] = simulate(op, vol, fixed_target(op.index, w, "monthly"))
    curves["fixed_vol_target_10"] = simulate(op, vol, volatility_baseline(cl))
    for code in CODES:
        w = [float(c == code) for c in CODES]
        target = pd.DataFrame(np.nan, index=op.index, columns=CODES)
        target.iloc[20] = w  # first daily-volume lookback is already available
        curves[f"hold_{code}"] = simulate(op, vol, target)
    for window in (120, 180):
        for kind in ("fixed", "trend", "risk", "both"):
            if kind in ("fixed", "risk") and window == 180:
                continue  # neither rule uses the trend window
            name = f"candidate_{kind}_{window}"
            target = (fixed_target(op.index, [.4, .2, .2], "weekly") if kind == "fixed"
                      else new_target(cl, kind, window))
            curves[name] = simulate(op, vol, target)
            for cost, lag in ((BASE_COST * 2, 1), (BASE_COST * 2, 2)):
                suffix = "cost2" if lag == 1 else "cost2_lag2"
                curves[f"stress_{kind}_{window}_{suffix}"] = simulate(op, vol, target, cost, lag)
    for window in (120, 180):
        target = candidate_weights(cl, shares, "flow", window, "weekly")
        # Blackout bars must invalidate the flow signal: the old helper treats
        # NaN as ineligible; split artifacts cannot become inflows.
        curves[f"legacy_flow_{window}"] = simulate(op, vol, target)
    evaluate(curves)
    print(pd.read_csv(OUT / "split_audit.csv").to_string(index=False))
    print(pd.read_csv(OUT / "results.csv").query("period == 'full' and strategy.str.startswith('candidate_')", engine="python")
          [["strategy", "total", "max_dd", "mean_exposure"]].to_string(index=False))
    print(pd.read_csv(OUT / "rolling_review.csv").to_string(index=False))


if __name__ == "__main__":
    run()
