from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import hashlib
import io
import math

import pandas as pd

import run_fxcm_untouched as fx

base = fx.base


def _fetch_one(year: int, week: int):
    url = f"https://candledata.fxcorporate.com/H1/GBPUSD/{year}/{week}.csv.gz"
    raw = fx._read_url(url)
    if raw is None:
        return None
    try:
        decoded = gzip.decompress(raw)
    except OSError:
        decoded = raw
    frame = pd.read_csv(io.BytesIO(decoded))
    return year, week, hashlib.sha256(raw).hexdigest(), fx._normalize(frame)


def parallel_load_data():
    jobs = [(y, w) for y in range(2012, 2020) for w in range(1, 54)]
    frames = []
    source_hashes = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(_fetch_one, y, w): (y, w) for y, w in jobs}
        for future in as_completed(futures):
            result = future.result()
            if result is None:
                continue
            _, _, sha, frame = result
            source_hashes.append(sha)
            frames.append(frame)
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
        "files_fetched": len(frames),
        "aggregate_source_sha256": hashlib.sha256("".join(sorted(source_hashes)).encode()).hexdigest(),
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


base.load_data = parallel_load_data
fx.main()
