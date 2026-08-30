"""
Universe construction.

IMPORTANT survivorship-bias warning: pulling "current S&P 500 constituents"
gives you a universe that already excludes every name that got delisted,
acquired, or kicked out for cratering -- exactly the ~39%-lost-money,
18.5%-lost-3/4-of-value cohort the whole thesis is built around. Backtesting
a "buy new highs" system against a survivorship-biased universe will
overstate performance, because you've silently deleted the losers before
the test even starts.

For a real backtest, use a point-in-time constituent list (data vendors:
Norgate Data, Sharadar/Nasdaq Data Link, or CRSP) that includes delisted
names. `get_sp500_wikipedia()` below is only good enough for (a) a live
watchlist going forward, or (b) a quick sanity-check backtest where you
explicitly accept the survivorship bias and read results accordingly.
"""

from __future__ import annotations

import io
from typing import List

import pandas as pd


def get_sp500_wikipedia() -> List[str]:
    """Current S&P 500 tickers, scraped from Wikipedia. Survivorship-biased
    (see module docstring) -- fine for a live watchlist, not for a clean
    historical backtest."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    tables = pd.read_html(url)
    df = tables[0]
    return sorted(df["Symbol"].str.replace(".", "-", regex=False).tolist())


def load_universe_csv(path: str, column: str = "symbol") -> List[str]:
    """Load a custom universe (ideally a point-in-time, delisting-inclusive
    list) from a CSV with a ticker column."""
    df = pd.read_csv(path)
    return sorted(df[column].dropna().unique().tolist())


def load_universe_txt(path: str) -> List[str]:
    """One ticker per line."""
    with open(path) as f:
        return sorted({line.strip().upper() for line in f if line.strip()})
