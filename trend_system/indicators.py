"""Technical indicators used by the screener and sizing modules."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=period).mean()


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def rolling_high(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=period).max()


def rolling_low(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period, min_periods=period).min()


def is_sma_rising(s: pd.Series, lookback: int) -> pd.Series:
    """True where s(t) > s(t - lookback)."""
    return s > s.shift(lookback)


def relative_strength(close: pd.Series, benchmark_close: pd.Series, lookback: int) -> pd.Series:
    """Trailing-`lookback`-day total return of the stock minus the
    benchmark's, aligned on the stock's index. Used only for cross-
    sectional ranking (see screener.rs_percentile_rank), not as an
    absolute signal."""
    stock_ret = close / close.shift(lookback) - 1.0
    bench_ret = benchmark_close.reindex(close.index).ffill()
    bench_ret = bench_ret / bench_ret.shift(lookback) - 1.0
    return stock_ret - bench_ret


def new_high_count(close: pd.Series, window: int) -> pd.Series:
    """Rolling count of days in the trailing `window` that were a new
    high-to-date at the time. Diagnostic only: the study's own tell for a
    compounder (Cisco 488 new highs, GE 1,011, MSFT 424 before their
    respective peaks) -- useful for post-hoc inspection of what the system
    caught, not a live entry signal itself (the breakout trigger in
    screener.py is the live analogue of this)."""
    to_date_high = close.cummax()
    is_new_high = (close >= to_date_high) & (close > close.shift(1).cummax())
    return is_new_high.rolling(window, min_periods=1).sum()
