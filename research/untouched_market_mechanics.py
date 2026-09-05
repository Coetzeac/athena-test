from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd
from scipy import stats

DATA_URL = "https://raw.githubusercontent.com/getdata-finance/gbpusd-1h-ohlcv-forex-historical-data/main/GBPUSD_1h.csv"
HOLDOUT_START = pd.Timestamp("2002-01-01T00:00:00Z")
HOLDOUT_END = pd.Timestamp("2019-12-31T23:00:00Z")
PIVOT_LEFT = 3
PIVOT_RIGHT = 3
ATR_LEN = 14
SWEEP_ATR = 0.10
DISPLACEMENT_MULT = 1.80
BODY_MA_LEN = 20
CONFIRM_WINDOW = 6
FORWARD_BARS = 12
COOLDOWN_BARS = 24
EFFECT_FLOOR = 0.05
ALPHA = 0.05
SEED = 20260905
OUTDIR = Path("runtime/research/untouched-market-mechanics-2002-2019")


def wilson_interval(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    z = stats.norm.ppf(1 - alpha / 2)
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return center - half, center + half


def fetch_bytes(url: str) -> bytes:
    with urlopen(url, timeout=120) as r:
        return r.read()


def load_data() -> tuple[pd.DataFrame, dict]:
    raw = fetch_bytes(DATA_URL)
    sha = hashlib.sha256(raw).hexdigest()
    tmp = OUTDIR / "source.csv"
    OUTDIR.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(raw)
    df = pd.read_csv(tmp, parse_dates=["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    required = ["datetime", "open", "high", "low", "close", "volume"]
    missing_cols = [c for c in required if c not in df.columns]
    if missing_cols:
        raise RuntimeError(f"Missing columns: {missing_cols}")
    q = {
        "sha256": sha,
        "rows_total": int(len(df)),
        "start": str(df.datetime.min()),
        "end": str(df.datetime.max()),
        "duplicates": int(df.datetime.duplicated().sum()),
        "nulls_ohlc": int(df[["open", "high", "low", "close"]].isna().sum().sum()),
        "ohlc_violations": int(((df.high < df[["open", "close", "low"]].max(axis=1)) | (df.low > df[["open", "close", "high"]].min(axis=1))).sum()),
    }
    if q["duplicates"] or q["nulls_ohlc"] or q["ohlc_violations"]:
        raise RuntimeError(f"Data-quality gate failed: {q}")
    h = df[(df.datetime >= HOLDOUT_START) & (df.datetime <= HOLDOUT_END)].copy().reset_index(drop=True)
    q["rows_holdout"] = int(len(h))
    q["holdout_start"] = str(h.datetime.min())
    q["holdout_end"] = str(h.datetime.max())
    if len(h) < 50000:
        raise RuntimeError(f"Unexpectedly short holdout: {len(h)}")
    return h, q


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    prev_close = x.close.shift(1)
    tr = pd.concat([
        x.high - x.low,
        (x.high - prev_close).abs(),
        (x.low - prev_close).abs(),
    ], axis=1).max(axis=1)
    x["atr"] = tr.rolling(ATR_LEN, min_periods=ATR_LEN).mean()
    x["body"] = (x.close - x.open).abs()
    x["body_ma"] = x.body.shift(1).rolling(BODY_MA_LEN, min_periods=BODY_MA_LEN).mean()

    highs = x.high.to_numpy()
    lows = x.low.to_numpy()
    n = len(x)
    pivot_high = np.zeros(n, dtype=bool)
    pivot_low = np.zeros(n, dtype=bool)
    for i in range(PIVOT_LEFT, n - PIVOT_RIGHT):
        wh = highs[i - PIVOT_LEFT:i + PIVOT_RIGHT + 1]
        wl = lows[i - PIVOT_LEFT:i + PIVOT_RIGHT + 1]
        pivot_high[i] = highs[i] == np.max(wh) and np.sum(wh == highs[i]) == 1
        pivot_low[i] = lows[i] == np.min(wl) and np.sum(wl == lows[i]) == 1
    x["pivot_high"] = pivot_high
    x["pivot_low"] = pivot_low
    return x


def extract_events(x: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    raids = []
    signals = []
    last_ph = math.nan
    last_pl = math.nan
    last_signal_i = -10**9
    active_raids: list[dict] = []

    for t in range(len(x)):
        confirm_i = t - PIVOT_RIGHT
        if confirm_i >= 0:
            if bool(x.pivot_high.iloc[confirm_i]):
                last_ph = float(x.high.iloc[confirm_i])
            if bool(x.pivot_low.iloc[confirm_i]):
                last_pl = float(x.low.iloc[confirm_i])

        atr = float(x.atr.iloc[t]) if pd.notna(x.atr.iloc[t]) else math.nan
        if not math.isfinite(atr) or atr <= 0:
            continue

        # First test pending raids for displacement confirmation. The raid bar itself cannot confirm.
        still_active = []
        for r in active_raids:
            age = t - r["raid_i"]
            if age < 1:
                still_active.append(r)
                continue
            if age > CONFIRM_WINDOW:
                continue
            body_ma = float(x.body_ma.iloc[t]) if pd.notna(x.body_ma.iloc[t]) else math.nan
            if not math.isfinite(body_ma) or body_ma <= 0:
                still_active.append(r)
                continue
            body_ok = float(x.body.iloc[t]) >= DISPLACEMENT_MULT * body_ma
            if r["direction"] == 1:
                directional = x.close.iloc[t] > x.open.iloc[t]
                structure_break = x.close.iloc[t] > r["raid_high"]
            else:
                directional = x.close.iloc[t] < x.open.iloc[t]
                structure_break = x.close.iloc[t] < r["raid_low"]
            if body_ok and directional and structure_break:
                if t - last_signal_i >= COOLDOWN_BARS:
                    signals.append({
                        **r,
                        "signal_i": t,
                        "signal_time": x.datetime.iloc[t],
                        "signal_close": float(x.close.iloc[t]),
                        "signal_atr": atr,
                        "confirm_delay": age,
                    })
                    last_signal_i = t
                # This raid is terminal once a valid confirmation occurs, even if cooldown blocks it.
                continue
            still_active.append(r)
        active_raids = still_active

        # Detect new sell-side and buy-side liquidity raids using only confirmed swings.
        if math.isfinite(last_pl):
            if x.low.iloc[t] < last_pl - SWEEP_ATR * atr and x.close.iloc[t] > last_pl:
                r = {
                    "raid_i": t,
                    "raid_time": x.datetime.iloc[t],
                    "direction": 1,
                    "swing_level": last_pl,
                    "raid_high": float(x.high.iloc[t]),
                    "raid_low": float(x.low.iloc[t]),
                    "raid_close": float(x.close.iloc[t]),
                    "raid_atr": atr,
                }
                raids.append(r.copy())
                active_raids.append(r)
        if math.isfinite(last_ph):
            if x.high.iloc[t] > last_ph + SWEEP_ATR * atr and x.close.iloc[t] < last_ph:
                r = {
                    "raid_i": t,
                    "raid_time": x.datetime.iloc[t],
                    "direction": -1,
                    "swing_level": last_ph,
                    "raid_high": float(x.high.iloc[t]),
                    "raid_low": float(x.low.iloc[t]),
                    "raid_close": float(x.close.iloc[t]),
                    "raid_atr": atr,
                }
                raids.append(r.copy())
                active_raids.append(r)

    return pd.DataFrame(raids), pd.DataFrame(signals)


def add_outcomes(events: pd.DataFrame, x: pd.DataFrame, anchor_col: str, close_col: str, atr_col: str) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    out = events.copy()
    signed_returns = []
    wins = []
    barriers = []
    for _, e in out.iterrows():
        i = int(e[anchor_col])
        d = int(e.direction)
        if i + FORWARD_BARS >= len(x):
            signed_returns.append(math.nan)
            wins.append(math.nan)
            barriers.append(math.nan)
            continue
        entry = float(e[close_col])
        atr = float(e[atr_col])
        end = float(x.close.iloc[i + FORWARD_BARS])
        sr = d * (end - entry) / atr
        signed_returns.append(sr)
        wins.append(float(sr > 0))

        target = entry + d * atr
        stop = entry - d * atr
        result = 0.0
        for j in range(i + 1, i + FORWARD_BARS + 1):
            h, l = float(x.high.iloc[j]), float(x.low.iloc[j])
            hit_t = h >= target if d == 1 else l <= target
            hit_s = l <= stop if d == 1 else h >= stop
            if hit_t and hit_s:
                result = 0.0  # conservative same-bar ambiguity -> failure
                break
            if hit_t:
                result = 1.0
                break
            if hit_s:
                result = 0.0
                break
        barriers.append(result)
    out["signed_return_atr_12"] = signed_returns
    out["directional_win_12"] = wins
    out["barrier_1atr_win_12"] = barriers
    return out.dropna(subset=["directional_win_12"])


def summarize(events: pd.DataFrame, label: str) -> dict:
    if events.empty:
        return {"label": label, "n": 0}
    n = int(len(events))
    k = int(events.directional_win_12.sum())
    hit = k / n
    ci = wilson_interval(k, n)
    bt = stats.binomtest(k, n, 0.5, alternative="greater")
    sr = events.signed_return_atr_12.to_numpy(dtype=float)
    mean_sr = float(np.mean(sr))
    median_sr = float(np.median(sr))
    sem = stats.sem(sr) if n > 1 else math.nan
    ttest = stats.ttest_1samp(sr, 0.0, alternative="greater") if n > 1 else None
    return {
        "label": label,
        "n": n,
        "wins": k,
        "hit_rate": hit,
        "lift_vs_50pp": hit - 0.5,
        "wilson_95_low": ci[0],
        "wilson_95_high": ci[1],
        "binomial_p_one_sided": float(bt.pvalue),
        "mean_signed_return_atr_12": mean_sr,
        "median_signed_return_atr_12": median_sr,
        "t_p_one_sided": float(ttest.pvalue) if ttest else math.nan,
        "barrier_1atr_hit_rate_12": float(events.barrier_1atr_win_12.mean()),
        "long_n": int((events.direction == 1).sum()),
        "short_n": int((events.direction == -1).sum()),
    }


def matched_controls(signals: pd.DataFrame, x: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    if signals.empty:
        return pd.DataFrame(), {"n": 0}
    rng = np.random.default_rng(SEED)
    z = x.copy()
    z["year"] = z.datetime.dt.year
    z["hour"] = z.datetime.dt.hour
    try:
        z["atr_decile"] = pd.qcut(z.atr.rank(method="first"), 10, labels=False, duplicates="drop")
    except Exception:
        z["atr_decile"] = 0
    signal_idx = set(int(v) for v in signals.signal_i)
    forbidden = np.zeros(len(z), dtype=bool)
    for i in signal_idx:
        forbidden[max(0, i-24):min(len(z), i+25)] = True
    rows = []
    for _, s in signals.iterrows():
        i = int(s.signal_i)
        if i + FORWARD_BARS >= len(z):
            continue
        yr = int(z.year.iloc[i]); hr = int(z.hour.iloc[i]); dec = z.atr_decile.iloc[i]
        mask = (z.year == yr) & (z.hour == hr) & (z.atr_decile == dec) & (~forbidden)
        candidates = np.flatnonzero(mask.to_numpy())
        candidates = candidates[candidates + FORWARD_BARS < len(z)]
        if len(candidates) == 0:
            continue
        c = int(rng.choice(candidates))
        d = int(s.direction)
        atr = float(z.atr.iloc[c])
        if not math.isfinite(atr) or atr <= 0:
            continue
        sr = d * (float(z.close.iloc[c + FORWARD_BARS]) - float(z.close.iloc[c])) / atr
        rows.append({"signal_i": i, "control_i": c, "direction": d, "control_signed_return_atr_12": sr, "control_win_12": float(sr > 0)})
    controls = pd.DataFrame(rows)
    if controls.empty:
        return controls, {"n": 0}
    merged = signals.merge(controls, on=["signal_i", "direction"], how="inner")
    diffs = merged.signed_return_atr_12 - merged.control_signed_return_atr_12
    paired = stats.ttest_rel(merged.signed_return_atr_12, merged.control_signed_return_atr_12, alternative="greater")
    return merged, {
        "n": int(len(merged)),
        "signal_hit_rate": float(merged.directional_win_12.mean()),
        "control_hit_rate": float(merged.control_win_12.mean()),
        "hit_rate_lift": float(merged.directional_win_12.mean() - merged.control_win_12.mean()),
        "signal_mean_signed_return_atr": float(merged.signed_return_atr_12.mean()),
        "control_mean_signed_return_atr": float(merged.control_signed_return_atr_12.mean()),
        "mean_paired_difference_atr": float(diffs.mean()),
        "paired_t_p_one_sided": float(paired.pvalue),
    }


def main() -> None:
    x, quality = load_data()
    x = add_features(x)
    raids, signals = extract_events(x)
    raids = add_outcomes(raids, x, "raid_i", "raid_close", "raid_atr")
    signals = add_outcomes(signals, x, "signal_i", "signal_close", "signal_atr")

    primary = summarize(signals, "full_mechanics")
    raid_only = summarize(raids, "raid_only")
    matched, matched_summary = matched_controls(signals, x)

    subperiods = {}
    for name, a, b in [
        ("2002-2007", 2002, 2007),
        ("2008-2013", 2008, 2013),
        ("2014-2019", 2014, 2019),
    ]:
        e = signals[(signals.signal_time.dt.year >= a) & (signals.signal_time.dt.year <= b)]
        subperiods[name] = summarize(e, name)

    long_summary = summarize(signals[signals.direction == 1], "long")
    short_summary = summarize(signals[signals.direction == -1], "short")

    consistency = all(v.get("n", 0) >= 20 and v.get("hit_rate", 0) > 0.5 for v in subperiods.values())
    meaningful = bool(
        primary.get("n", 0) >= 100
        and primary.get("lift_vs_50pp", -1) >= EFFECT_FLOOR
        and primary.get("wilson_95_low", 0) > 0.5
        and primary.get("binomial_p_one_sided", 1) < ALPHA
        and consistency
    )

    result = {
        "test_specification": {
            "instrument": "GBPUSD",
            "timeframe": "1h",
            "holdout": [str(HOLDOUT_START), str(HOLDOUT_END)],
            "pivot_left": PIVOT_LEFT,
            "pivot_right_confirmation_delay": PIVOT_RIGHT,
            "sweep_min_atr": SWEEP_ATR,
            "displacement_body_vs_prior20_mean": DISPLACEMENT_MULT,
            "confirmation_window_bars": CONFIRM_WINDOW,
            "forward_bars": FORWARD_BARS,
            "cooldown_bars": COOLDOWN_BARS,
            "primary_null_hit_rate": 0.5,
            "minimum_meaningful_lift_pp": EFFECT_FLOOR,
            "alpha": ALPHA,
            "same_bar_target_stop_rule": "failure (conservative)",
            "parameter_search_on_holdout": False,
        },
        "data_quality": quality,
        "counts": {"raids": int(len(raids)), "confirmed_full_signals": int(len(signals))},
        "primary": primary,
        "raid_only": raid_only,
        "matched_control": matched_summary,
        "long": long_summary,
        "short": short_summary,
        "subperiods": subperiods,
        "decision_rule": {
            "n_at_least_100": primary.get("n", 0) >= 100,
            "lift_at_least_5pp": primary.get("lift_vs_50pp", -1) >= EFFECT_FLOOR,
            "wilson_low_above_50pct": primary.get("wilson_95_low", 0) > 0.5,
            "one_sided_p_below_0_05": primary.get("binomial_p_one_sided", 1) < ALPHA,
            "all_subperiods_n_at_least_20_and_above_50pct": consistency,
        },
        "verdict": "PREDICTIVE_INFORMATION_SUPPORTED" if meaningful else "PREDICTIVE_INFORMATION_NOT_ESTABLISHED",
    }

    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "results.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    signals.to_csv(OUTDIR / "signals.csv", index=False)
    raids.to_csv(OUTDIR / "raids.csv", index=False)
    matched.to_csv(OUTDIR / "matched_controls.csv", index=False)

    p = primary
    md = f"""# Untouched GBPUSD Market-Mechanics Test\n\n**Verdict:** {result['verdict']}\n\n## Frozen primary test\n- Holdout: 2002-01-01 through 2019-12-31 UTC\n- Signal: confirmed 3/3 swing sweep by >=0.10 ATR and close back inside, followed within 6 bars by directional displacement with body >=1.8x prior-20-bar mean and close beyond raid-bar extreme.\n- Cooldown: 24 bars.\n- Primary endpoint: direction-adjusted 12-bar close return > 0.\n- Null: 50% directional hit rate.\n- Meaningful threshold: >=5 percentage-point lift, 95% Wilson lower bound >50%, one-sided exact binomial p<0.05, n>=100, and all three six-year subperiods n>=20 with hit rate >50%.\n\n## Primary result\n- n: {p.get('n')}\n- Hit rate: {p.get('hit_rate', float('nan')):.2%}\n- Lift vs 50%: {p.get('lift_vs_50pp', float('nan')):.2%}\n- 95% Wilson CI: [{p.get('wilson_95_low', float('nan')):.2%}, {p.get('wilson_95_high', float('nan')):.2%}]\n- One-sided exact binomial p: {p.get('binomial_p_one_sided', float('nan')):.6g}\n- Mean direction-adjusted 12h return: {p.get('mean_signed_return_atr_12', float('nan')):.4f} ATR\n- 1-ATR-before-1-ATR barrier hit rate (12 bars): {p.get('barrier_1atr_hit_rate_12', float('nan')):.2%}\n\n## Controls\n- Raid-only hit rate: {raid_only.get('hit_rate', float('nan')):.2%} (n={raid_only.get('n')})\n- Matched nonsignal control hit rate: {matched_summary.get('control_hit_rate', float('nan')):.2%} (n={matched_summary.get('n')})\n- Full-signal matched hit-rate lift: {matched_summary.get('hit_rate_lift', float('nan')):.2%}\n- Paired mean-return test p: {matched_summary.get('paired_t_p_one_sided', float('nan')):.6g}\n\n## Stability\n"""
    for name, s in subperiods.items():
        md += f"- {name}: {s.get('hit_rate', float('nan')):.2%}, n={s.get('n')}\n"
    md += "\n## Data integrity\n"
    md += f"- Source SHA-256: `{quality['sha256']}`\n- Total source rows: {quality['rows_total']}\n- Holdout rows: {quality['rows_holdout']}\n- Duplicate timestamps: {quality['duplicates']}\n- OHLC violations: {quality['ohlc_violations']}\n"
    (OUTDIR / "report.md").write_text(md, encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
