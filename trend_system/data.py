"""
Daily OHLCV data loading with a local parquet cache, so repeated backtests
don't re-hit the network for every run.

Default backend is yfinance. Swap in a paid vendor (Polygon, Norgate,
Sharadar) by writing a class with the same `.get(symbols, start, end)`
interface -- the rest of the system only depends on that interface, not on
yfinance specifically.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Optional

import pandas as pd


class YFinanceLoader:
    def __init__(self, cache_dir: str = ".cache/prices"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}.parquet"

    def _fetch_one(self, symbol: str, start: str, end: Optional[str]) -> pd.DataFrame:
        import yfinance as yf

        df = yf.download(
            symbol, start=start, end=end, progress=False, auto_adjust=True
        )
        if df.empty:
            return df
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index.name = "date"
        return df[["Open", "High", "Low", "Close", "Volume"]]

    def get(
        self,
        symbols: Iterable[str],
        start: str,
        end: Optional[str] = None,
        refresh: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        out: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            path = self._cache_path(symbol)
            if path.exists() and not refresh:
                df = pd.read_parquet(path)
            else:
                try:
                    df = self._fetch_one(symbol, start, end)
                except Exception as exc:  # network hiccup on one symbol shouldn't kill the run
                    print(f"[data] skip {symbol}: {exc}")
                    continue
                if df.empty:
                    continue
                df.to_parquet(path)
            if not df.empty:
                out[symbol] = df
        return out


class SyntheticLoader:
    """Deterministic synthetic OHLCV generator for offline testing of the
    screener/sizing/backtest logic when there's no market data access
    (e.g. this dev sandbox has no outbound network to Yahoo Finance). Do
    NOT use this for anything you'd draw real conclusions from -- it's a
    smoke test, not a market simulation.
    """

    def __init__(self, seed: int = 0):
        import numpy as np

        self.rng = np.random.default_rng(seed)

    def get(
        self,
        symbols: Iterable[str],
        start: str,
        end: Optional[str] = None,
        refresh: bool = False,
    ) -> Dict[str, pd.DataFrame]:
        import numpy as np

        dates = pd.bdate_range(start=start, end=end or "2023-12-31")
        out: Dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            n = len(dates)
            # Mixture: most names drift slightly down/flat with noise (the
            # "64% underperform" cohort), a minority get a strong sustained
            # trend (the tail winners), matching the shape this system is
            # designed to catch -- purely for exercising the code path.
            regime = self.rng.choice(["loser", "flat", "winner"], p=[0.45, 0.35, 0.20])
            drift = {"loser": -0.0006, "flat": 0.0000, "winner": 0.0011}[regime]
            vol = 0.02
            rets = self.rng.normal(drift, vol, n)
            price = 20 * np.exp(np.cumsum(rets))
            high = price * (1 + np.abs(self.rng.normal(0, 0.005, n)))
            low = price * (1 - np.abs(self.rng.normal(0, 0.005, n)))
            openp = price * (1 + self.rng.normal(0, 0.002, n))
            vol_series = self.rng.integers(200_000, 5_000_000, n)
            df = pd.DataFrame(
                {"Open": openp, "High": high, "Low": low, "Close": price, "Volume": vol_series},
                index=dates,
            )
            df.index.name = "date"
            out[symbol] = df
        return out
