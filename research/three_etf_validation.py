"""Reproducible, exploratory three-ETF backtest. No live orders.

Wind front-adjusted OHLC is downloaded in calendar-year chunks. Signals use
information at the preceding close; positions change at the next open. The
research output is deliberately separate from the project's existing engine.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "three_etf_validation"
WIND = Path(r"C:\Users\Administrator\.agents\skills\wind-mcp-skill")
CODES = ("512890", "513130", "512480")
START, END = "20210601", "20260915"
TEST_START = "2025-01-01"
BASE_COST = 0.001  # each side: commission + spread/slippage proxy, not observed fills


def fetch_wind() -> pd.DataFrame:
    """Use only the documented fund_data K-line route, one ETF per call."""
    rows = []
    for code in CODES:
        for year in range(2021, 2027):
            begin, end = max(START, f"{year}0101"), min(END, f"{year}1231")
            if begin > end:
                continue
            params = {"windcode": f"{code}.SH", "begin_date": begin,
                      "end_date": end, "period": "1d", "aftime": "0"}
            for attempt in range(3):
                proc = subprocess.run(
                    ["node", "scripts/cli.mjs", "call", "fund_data",
                     "get_fund_kline", json.dumps(params, ensure_ascii=False)],
                    cwd=WIND, capture_output=True, timeout=90,
                )
                if proc.returncode == 0:
                    break
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
            if proc.returncode:
                raise RuntimeError(f"Wind {code} {year}: {proc.stdout.decode('utf-8', 'replace')}")
            response = json.loads(proc.stdout.decode("utf-8"))
            data = json.loads(response["content"][0]["text"])
            if data.get("error"):
                raise RuntimeError(f"Wind {code} {year}: {data['error']}")
            names = [col["name"] for col in data["data"]["columns"]]
            for values in data["data"]["rows"]:
                item = dict(zip(names, values))
                rows.append({"date": item["TIME"][:10], "code": code,
                             "open": item["OPEN"], "close": item["MATCH"],
                             "volume": item["VOLUME"]})
            print(f"Wind {code} {year}: {len(data['data']['rows'])} rows", flush=True)
    df = pd.DataFrame(rows)
    for col in ("open", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values(["code", "date"]).drop_duplicates(["code", "date"])
    df.to_csv(OUT / "wind_adjusted_ohlc.csv", index=False, encoding="utf-8-sig")
    return df


def load_shares(dates: pd.Index) -> pd.DataFrame:
    conn = sqlite3.connect(f"file:{ROOT / 'etf.db'}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT date,code,shares FROM fund_scale WHERE code IN ('512890','513130','512480')",
        conn,
    )
    conn.close()
    shares = df.pivot(index="date", columns="code", values="shares").reindex(dates)
    # A conservative two-session availability lag, including publication lag.
    return shares.ffill().shift(2)


def monthly_or_weekly_dates(dates: pd.Index, frequency: str) -> set[str]:
    idx = pd.to_datetime(dates)
    periods = idx.to_period("W-FRI" if frequency == "weekly" else "M")
    return set(pd.Series(dates, index=dates).groupby(periods).last())


def candidate_weights(close: pd.DataFrame, shares: pd.DataFrame,
                      family: str, window: int, frequency: str) -> pd.DataFrame:
    """Targets calculated at the close; never consumed until next day's open."""
    dates = close.index
    targets = pd.DataFrame(np.nan, index=dates, columns=CODES)
    decision_days = monthly_or_weekly_dates(dates, frequency)
    momentum = close / close.shift(window) - 1
    ma = close.rolling(window, min_periods=window).mean()
    vol = close.pct_change().rolling(60, min_periods=60).std() * np.sqrt(252)
    share_growth = shares / shares.shift(20) - 1
    for date in dates:
        if date not in decision_days:
            continue
        available = close.loc[date].notna() & ma.loc[date].notna()
        eligible = available & (close.loc[date] > ma.loc[date]) & (momentum.loc[date] > 0)
        w = pd.Series(0.0, index=CODES)
        if family == "trend":
            if eligible.any():
                w[eligible] = 0.8 / eligible.sum()
        elif family == "momentum":
            if eligible.any():
                best = momentum.loc[date].where(eligible).idxmax()
                w[best] = 0.7
        elif family in ("risk", "flow"):
            if family == "flow":
                eligible &= share_growth.loc[date].fillna(-1) > 0
            inv = (1 / vol.loc[date]).where(eligible).replace([np.inf, -np.inf], np.nan).fillna(0)
            if inv.sum() > 0:
                w = (0.8 * inv / inv.sum()).clip(upper=pd.Series({
                    "512890": 0.4, "513130": 0.25, "512480": 0.25,
                }))
                if w["513130"] + w["512480"] > 0.4:
                    w[["513130", "512480"]] *= 0.4 / (w["513130"] + w["512480"])
        else:
            raise ValueError(family)
        targets.loc[date] = w
    return targets


def simulate(open_px: pd.DataFrame, volume: pd.DataFrame,
             targets_close: pd.DataFrame, cost: float, signal_lag: int = 1) -> pd.DataFrame:
    """At open i use signal from close i-1; earn open i -> open i+1.

    Drifted holdings are rebased to NAV each morning. Untradeable ETF changes
    are rejected rather than imputed as fills. Fractional shares are a proxy.
    """
    target = targets_close.shift(signal_lag)
    dates = open_px.index
    actual = pd.Series(0.0, index=CODES)
    nav = 1.0
    records = []
    for i, date in enumerate(dates[:-1]):
        desired = actual.copy()
        if target.loc[date].notna().any():
            desired = target.loc[date].copy()
            tradable = (open_px.loc[date] > 0) & (volume.loc[date] > 0)
            desired[~tradable] = actual[~tradable]
        turnover = float((desired - actual).abs().sum())
        nav *= 1 - cost * turnover
        ret = (open_px.iloc[i + 1] / open_px.iloc[i] - 1).replace([np.inf, -np.inf], np.nan)
        if (desired[(desired > 0)] > 0).any() and ret[desired > 0].isna().any():
            raise ValueError(f"Missing return for held ETF at {date}")
        gross = float((desired * ret.fillna(0)).sum())
        nav *= 1 + gross
        gross_positions = desired * (1 + ret.fillna(0))
        actual = gross_positions / (1 + gross)
        records.append({"date": dates[i + 1], "nav": nav, "return": gross - cost * turnover,
                        "turnover": turnover, "cost": cost * turnover,
                        **{f"w_{c}": desired[c] for c in CODES}})
    return pd.DataFrame(records).set_index("date")


def measure(frame: pd.DataFrame, begin: str, end: str) -> dict:
    sample = frame.loc[begin:end]
    if len(sample) < 10:
        return {}
    preceding = frame.nav.shift(1).loc[sample.index[0]]
    nav = sample.nav / (preceding if pd.notna(preceding) else 1.0)
    years = len(sample) / 252
    rets = nav.pct_change().dropna()
    return {"total": float(nav.iloc[-1] - 1),
            "cagr": float(nav.iloc[-1] ** (1 / years) - 1),
            "max_dd": float((nav / nav.cummax() - 1).min()),
            "sharpe": float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0,
            "turnover": float(sample.turnover.sum()),
            "cost_sum": float(sample.cost.sum()),
            "trade_days": int((sample.turnover > 1e-8).sum()),
            "days": len(sample)}


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    raw = (pd.read_csv(OUT / "wind_adjusted_ohlc.csv", dtype={"code": str})
           if "--cached" in sys.argv else fetch_wind())
    op = raw.pivot(index="date", columns="code", values="open").reindex(columns=CODES)
    cl = raw.pivot(index="date", columns="code", values="close").reindex(op.index, columns=CODES)
    volume = raw.pivot(index="date", columns="code", values="volume").reindex(op.index, columns=CODES)
    start = max(raw.loc[raw.code == c, "date"].min() for c in CODES)
    op, cl, volume = (x.loc[start:] for x in (op, cl, volume))
    shares = load_shares(op.index)
    quality = {"source": "Wind fund_data.get_fund_kline, front adjusted (aftime=0)",
               "first": start, "last": op.index[-1], "rows": len(op),
               "missing_open": op.isna().sum().to_dict(),
               "missing_close": cl.isna().sum().to_dict(),
               "zero_or_missing_volume": ((volume <= 0) | volume.isna()).sum().to_dict(),
               "extreme_close_returns": []}
    for code in CODES:
        for date, value in cl[code].pct_change().items():
            if pd.notna(value) and abs(value) > 0.12:
                quality["extreme_close_returns"].append({"code": code, "date": date, "return": value})
    (OUT / "quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
    if any(quality["missing_open"].values()) or any(quality["extreme_close_returns"]):
        raise ValueError("Price quality gate failed; see quality.json")

    trials = [(family, window, frequency)
              for family, windows in (("trend", (60, 120, 180)),
                                      ("momentum", (60, 120)),
                                      ("risk", (120, 180)),
                                      ("flow", (120,)))
              for window in windows for frequency in ("weekly", "monthly")]
    results = []
    curves = {}
    for family, window, frequency in trials:
        label = f"{family}_{window}_{frequency}"
        target = candidate_weights(cl, shares, family, window, frequency)
        for multiplier in (1, 2):
            curve = simulate(op, volume, target, BASE_COST * multiplier)
            if multiplier == 1:
                curves[label] = curve
            for period, begin, end in (("development", "2021-12-01", "2023-12-31"),
                                       ("validation", "2024-01-01", "2024-12-31"),
                                       ("historical_holdout", TEST_START, "2026-09-15")):
                results.append({"strategy": label, "family": family, "window": window,
                                "frequency": frequency, "cost_x": multiplier, "period": period,
                                **measure(curve, begin, end)})
    # Buy-and-hold: target only at the initial close, then fractions drift.
    for code in CODES:
        target = pd.DataFrame(np.nan, index=op.index, columns=CODES)
        target.loc[op.index[0]] = 0.0
        target.loc[op.index[0], code] = 1.0
        curve = simulate(op, volume, target, BASE_COST)
        curves[f"hold_{code}"] = curve
        for period, begin, end in (("development", "2021-12-01", "2023-12-31"),
                                   ("validation", "2024-01-01", "2024-12-31"),
                                   ("historical_holdout", TEST_START, "2026-09-15")):
            results.append({"strategy": f"hold_{code}", "family": "benchmark",
                            "window": 0, "frequency": "none", "cost_x": 1, "period": period,
                            **measure(curve, begin, end)})
    equal = pd.DataFrame(np.nan, index=op.index, columns=CODES)
    equal.loc[op.index[0]] = 1 / 3
    curves["equal_weight"] = simulate(op, volume, equal, BASE_COST)
    for period, begin, end in (("development", "2021-12-01", "2023-12-31"),
                               ("validation", "2024-01-01", "2024-12-31"),
                               ("historical_holdout", TEST_START, "2026-09-15")):
        results.append({"strategy": "equal_weight", "family": "benchmark", "window": 0,
                        "frequency": "buy_and_hold", "cost_x": 1, "period": period,
                        **measure(curves["equal_weight"], begin, end)})
    pd.DataFrame(results).to_csv(OUT / "all_results.csv", index=False, encoding="utf-8-sig")
    stress = []
    for family, window in (("risk", 120), ("risk", 180),
                           ("trend", 120), ("trend", 180)):
        label = f"{family}_{window}_weekly"
        target = candidate_weights(cl, shares, family, window, "weekly")
        delayed = simulate(op, volume, target, BASE_COST * 2, signal_lag=2)
        for period, begin, end in (("development", "2021-12-01", "2023-12-31"),
                                   ("validation", "2024-01-01", "2024-12-31"),
                                   ("historical_holdout", TEST_START, "2026-09-15")):
            stress.append({"strategy": label, "period": period,
                           "signal_lag_sessions": 2, "cost_x": 2,
                           **measure(delayed, begin, end)})
    pd.DataFrame(stress).to_csv(OUT / "delay_and_cost_stress.csv", index=False, encoding="utf-8-sig")
    for name, curve in curves.items():
        curve.to_csv(OUT / f"nav_{name}.csv", encoding="utf-8-sig")
    print(pd.DataFrame(results).query("period == 'historical_holdout' and cost_x == 1")
          [["strategy", "total", "cagr", "max_dd", "sharpe", "trade_days"]]
          .to_string(index=False), flush=True)


if __name__ == "__main__":
    run()
