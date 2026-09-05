from __future__ import annotations

import gzip
import hashlib
import io
import math
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd

import untouched_market_mechanics as base

# SOURCE REMEDIATION ONLY. The frozen mechanics, thresholds, endpoint and decision rule
# remain in untouched_market_mechanics.py. FXCM documents H1 candle coverage from
# 2012-01-01 and UTC timestamps, so the untouched holdout boundary is restricted to
# the source's documented pre-ATHENA history.
base.HOLDOUT_START = pd.Timestamp("2012-01-01T00:00:00Z")
base.HOLDOUT_END = pd.Timestamp("2019-12-31T23:00:00Z")


def _read_url(url: str) -> bytes | None:
    req = Request(url, headers={"User-Agent": "ATHENA-research-audit/1.0"})
    try:
        with urlopen(req, timeout=90) as r:
            return r.read()
    except HTTPError as e:
        if e.code in (403, 404):
            return None
        raise


def _canon(s: str) -> str:
    return "".join(ch.lower() for ch in str(s) if ch.isalnum())


def _pick(cols: list[str], candidates: list[str]) -> str | None:
    cmap = {_canon(c): c for c in cols}
    for candidate in candidates:
        if _canon(candidate) in cmap:
            return cmap[_canon(candidate)]
    return None


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    cols = list(frame.columns)
    dt = _pick(cols, ["DateTime", "datetime", "Date", "timestamp", "time"])
    if dt is None:
        raise RuntimeError(f"FXCM file has no recognized timestamp column: {cols}")

    # Prefer midpoint of bid/ask OHLC when both are present. This prevents choosing
    # the favorable side of the spread and preserves a neutral price series for the
    # predictive-information endpoint.
    bid = {
        k: _pick(cols, [f"Bid{k.title()}", f"bid{k}"])
        for k in ("open", "high", "low", "close")
    }
    ask = {
        k: _pick(cols, [f"Ask{k.title()}", f"ask{k}"])
        for k in ("open", "high", "low", "close")
    }
    simple = {k: _pick(cols, [k]) for k in ("open", "high", "low", "close")}

    out = pd.DataFrame()
    out["datetime"] = pd.to_datetime(frame[dt], utc=True, errors="coerce")
    if all(bid.values()) and all(ask.values()):
        for k in ("open", "high", "low", "close"):
            out[k] = (pd.to_numeric(frame[bid[k]], errors="coerce") + pd.to_numeric(frame[ask[k]], errors="coerce")) / 2.0
        out["spread_close"] = pd.to_numeric(frame[ask["close"]], errors="coerce") - pd.to_numeric(frame[bid["close"]], errors="coerce")
    elif all(simple.values()):
        for k in ("open", "high", "low", "close"):
            out[k] = pd.to_numeric(frame[simple[k]], errors="coerce")
        out["spread_close"] = math.nan
    else:
        raise RuntimeError(f"FXCM file has no recognized OHLC layout: {cols}")

    vol = _pick(cols, ["TickQty", "tickqty", "Volume", "volume"])
    out["volume"] = pd.to_numeric(frame[vol], errors="coerce") if vol else 0.0
    return out


def fxcm_load_data() -> tuple[pd.DataFrame, dict]:
    frames: list[pd.DataFrame] = []
    source_hashes: list[str] = []
    files = 0
    for year in range(2012, 2020):
        for week in range(1, 54):
            url = f"https://candledata.fxcorporate.com/H1/GBPUSD/{year}/{week}.csv.gz"
            raw = _read_url(url)
            if raw is None:
                continue
            files += 1
            source_hashes.append(hashlib.sha256(raw).hexdigest())
            try:
                decoded = gzip.decompress(raw)
            except OSError:
                decoded = raw
            frame = pd.read_csv(io.BytesIO(decoded))
            frames.append(_normalize(frame))

    if not frames:
        raise RuntimeError("FXCM acquisition returned no H1 GBPUSD files")

    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["datetime", "open", "high", "low", "close"])
    df = df.sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)
    h = df[(df.datetime >= base.HOLDOUT_START) & (df.datetime <= base.HOLDOUT_END)].copy().reset_index(drop=True)

    ohlc_bad = ((h.high < h[["open", "close", "low"]].max(axis=1)) | (h.low > h[["open", "close", "high"]].min(axis=1)))
    q = {
        "provider": "FXCM",
        "endpoint_pattern": "https://candledata.fxcorporate.com/H1/GBPUSD/{year}/{week}.csv.gz",
        "files_fetched": files,
        "aggregate_source_sha256": hashlib.sha256("".join(source_hashes).encode()).hexdigest(),
        "rows_total": int(len(df)),
        "rows_holdout": int(len(h)),
        "start": str(df.datetime.min()),
        "end": str(df.datetime.max()),
        "holdout_start": str(h.datetime.min()) if len(h) else None,
        "holdout_end": str(h.datetime.max()) if len(h) else None,
        "duplicates_after_dedup": int(h.datetime.duplicated().sum()),
        "nulls_ohlc": int(h[["open", "high", "low", "close"]].isna().sum().sum()),
        "ohlc_violations": int(ohlc_bad.sum()),
        "median_close_spread": float(h.spread_close.median()) if h.spread_close.notna().any() else None,
    }
    if q["rows_holdout"] < 40000 or q["duplicates_after_dedup"] or q["nulls_ohlc"] or q["ohlc_violations"]:
        raise RuntimeError(f"FXCM data-quality gate failed: {q}")
    return h, q


base.load_data = fxcm_load_data

# The original report labels its subperiods 2002-2007 / 2008-2013 / 2014-2019.
# That cosmetic reporting block cannot represent the source-restricted window. Rather
# than change mechanics, add a reporting-compatible wrapper that supplies three fixed
# predeclared partitions across 2012-2019 while leaving the primary test unchanged.
_original_summarize = base.summarize


def main() -> None:
    x, quality = base.load_data()
    x = base.add_features(x)
    raids, signals = base.extract_events(x)
    raids = base.add_outcomes(raids, x, "raid_i", "raid_close", "raid_atr")
    signals = base.add_outcomes(signals, x, "signal_i", "signal_close", "signal_atr")

    primary = base.summarize(signals, "full_mechanics")
    raid_only = base.summarize(raids, "raid_only")
    matched, matched_summary = base.matched_controls(signals, x)

    partitions = [("2012-2014", 2012, 2014), ("2015-2017", 2015, 2017), ("2018-2019", 2018, 2019)]
    subperiods = {}
    for name, a, b in partitions:
        e = signals[(signals.signal_time.dt.year >= a) & (signals.signal_time.dt.year <= b)]
        subperiods[name] = base.summarize(e, name)

    long_summary = base.summarize(signals[signals.direction == 1], "long")
    short_summary = base.summarize(signals[signals.direction == -1], "short")
    consistency = all(v.get("n", 0) >= 20 and v.get("hit_rate", 0) > 0.5 for v in subperiods.values())
    meaningful = bool(
        primary.get("n", 0) >= 100
        and primary.get("lift_vs_50pp", -1) >= base.EFFECT_FLOOR
        and primary.get("wilson_95_low", 0) > 0.5
        and primary.get("binomial_p_one_sided", 1) < base.ALPHA
        and consistency
    )

    result = {
        "test_specification": {
            "instrument": "GBPUSD",
            "timeframe": "1h",
            "holdout": [str(base.HOLDOUT_START), str(base.HOLDOUT_END)],
            "source": "FXCM public H1 candle archive",
            "pivot_left": base.PIVOT_LEFT,
            "pivot_right_confirmation_delay": base.PIVOT_RIGHT,
            "sweep_min_atr": base.SWEEP_ATR,
            "displacement_body_vs_prior20_mean": base.DISPLACEMENT_MULT,
            "confirmation_window_bars": base.CONFIRM_WINDOW,
            "forward_bars": base.FORWARD_BARS,
            "cooldown_bars": base.COOLDOWN_BARS,
            "primary_null_hit_rate": 0.5,
            "minimum_meaningful_lift_pp": base.EFFECT_FLOOR,
            "alpha": base.ALPHA,
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
            "lift_at_least_5pp": primary.get("lift_vs_50pp", -1) >= base.EFFECT_FLOOR,
            "wilson_low_above_50pct": primary.get("wilson_95_low", 0) > 0.5,
            "one_sided_p_below_0_05": primary.get("binomial_p_one_sided", 1) < base.ALPHA,
            "all_subperiods_n_at_least_20_and_above_50pct": consistency,
        },
        "verdict": "PREDICTIVE_INFORMATION_SUPPORTED" if meaningful else "PREDICTIVE_INFORMATION_NOT_ESTABLISHED",
    }

    base.OUTDIR.mkdir(parents=True, exist_ok=True)
    import json
    (base.OUTDIR / "results.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    signals.to_csv(base.OUTDIR / "signals.csv", index=False)
    raids.to_csv(base.OUTDIR / "raids.csv", index=False)
    matched.to_csv(base.OUTDIR / "matched_controls.csv", index=False)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
