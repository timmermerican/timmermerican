"""
Selection logic: "let price action nominate candidates" instead of
forecasting winners.

Two-stage filter, evaluated per symbol per day using only data available up
to and including that day (no look-ahead):

  1. Trend template -- a structural pass/fail on trend health (Minervini /
     Weinstein stage-2 criteria). This alone does not trigger a trade; it
     just defines the eligible pool.
  2. Breakout trigger -- fires only for names already in the eligible pool,
     on a new N-day high. This is the live, non-hindsight analogue of the
     "winners make new highs disproportionately often" tell from the
     study.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import indicators as ind
from .config import Config


@dataclass
class SymbolFeatures:
    close: pd.Series
    sma_short: pd.Series
    sma_mid: pd.Series
    sma_long: pd.Series
    high_252: pd.Series
    low_252: pd.Series
    atr: pd.Series
    breakout_high: pd.Series
    rs: pd.Series


def compute_features(df: pd.DataFrame, benchmark_close: pd.Series, cfg: Config) -> SymbolFeatures:
    close = df["Close"]
    return SymbolFeatures(
        close=close,
        sma_short=ind.sma(close, cfg.sma_short),
        sma_mid=ind.sma(close, cfg.sma_mid),
        sma_long=ind.sma(close, cfg.sma_long),
        high_252=ind.rolling_high(close, 252),
        low_252=ind.rolling_low(close, 252),
        atr=ind.atr(df, cfg.atr_period),
        breakout_high=ind.rolling_high(close, cfg.breakout_lookback_days).shift(1),
        rs=ind.relative_strength(close, benchmark_close, cfg.rs_lookback_days),
    )


def trend_template_pass(feat: SymbolFeatures, i: int, cfg: Config) -> bool:
    """Evaluate the trend template at integer position i in the series."""
    c = feat.close.iloc[i]
    s50, s150, s200 = feat.sma_short.iloc[i], feat.sma_mid.iloc[i], feat.sma_long.iloc[i]
    hi, lo = feat.high_252.iloc[i], feat.low_252.iloc[i]

    if pd.isna([c, s50, s150, s200, hi, lo]).any():
        return False

    checks = [
        c > s50 > s150 > s200,
        feat.sma_long.iloc[max(0, i - cfg.sma_long_trend_lookback_days)] < s200,
        c >= lo * (1 + cfg.pct_above_52wk_low_min),
        c >= hi * (1 - cfg.pct_below_52wk_high_max),
    ]
    return all(checks)


def rs_percentile_rank(rs_today: dict[str, float]) -> dict[str, float]:
    """Cross-sectional percentile rank of relative strength across the
    universe for a single date. `rs_today` maps symbol -> RS value (may
    contain NaNs, which are dropped from ranking and rank 0.0)."""
    s = pd.Series(rs_today).dropna()
    if s.empty:
        return {sym: 0.0 for sym in rs_today}
    ranks = s.rank(pct=True)
    return {sym: float(ranks.get(sym, 0.0)) for sym in rs_today}


def breakout_trigger(feat: SymbolFeatures, i: int) -> bool:
    c = feat.close.iloc[i]
    prior_high = feat.breakout_high.iloc[i]
    if pd.isna(c) or pd.isna(prior_high):
        return False
    return c > prior_high
