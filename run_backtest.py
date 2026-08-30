#!/usr/bin/env python3
"""CLI entry point.

Examples
--------
Real backtest against the current S&P 500 (survivorship-biased -- see
trend_system/universe.py) using yfinance data, 2015-present:

    python run_backtest.py --universe sp500 --start 2015-01-01 --equity 100000 --sleeve-pct 0.20

Offline smoke test (no network) using synthetic data, to sanity check the
engine itself:

    python run_backtest.py --synthetic --n-symbols 60 --start 2015-01-01
"""

from __future__ import annotations

import argparse

from trend_system.backtest import run_backtest
from trend_system.config import Config
from trend_system.data import LocalParquetLoader, SyntheticLoader, YFinanceLoader
from trend_system.metrics import print_summary, summarize
from trend_system.universe import get_sp500_wikipedia, load_universe_txt


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--universe", choices=["sp500", "file", "cache"], default="sp500")
    p.add_argument("--universe-file", default=None, help="txt file, one ticker per line")
    p.add_argument("--price-cache-dir", default=None,
                    help="directory of {SYMBOL}.parquet files (sma-scanner cache layout); "
                         "implies --universe cache if --universe not set explicitly")
    p.add_argument("--synthetic", action="store_true", help="use synthetic data, no network")
    p.add_argument("--n-symbols", type=int, default=50, help="synthetic mode only")
    p.add_argument("--start", default="2015-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--equity", type=float, default=100_000.0)
    p.add_argument("--sleeve-pct", type=float, default=0.20)
    p.add_argument("--out", default="backtest_trades.csv")
    args = p.parse_args()

    cfg = Config(
        total_equity=args.equity,
        active_sleeve_pct=args.sleeve_pct,
        start_date=args.start,
        end_date=args.end,
    )

    if args.synthetic:
        symbols = [f"SYN{i:03d}" for i in range(args.n_symbols)]
        loader = SyntheticLoader(seed=42)
        price_data = loader.get(symbols, start=args.start, end=args.end or "2023-12-31")
        bench_df = loader.get(["BENCH"], start=args.start, end=args.end or "2023-12-31")["BENCH"]
        benchmark_close = bench_df["Close"]
    elif args.price_cache_dir or args.universe == "cache":
        if not args.price_cache_dir:
            p.error("--price-cache-dir is required with --universe cache")
        loader = LocalParquetLoader(args.price_cache_dir)
        symbols = loader.available_symbols()
        price_data = loader.get(symbols, start=args.start, end=args.end)
        if cfg.benchmark_symbol not in price_data:
            p.error(f"benchmark {cfg.benchmark_symbol} not found in {args.price_cache_dir}")
        benchmark_close = price_data[cfg.benchmark_symbol]["Close"]
    else:
        if args.universe == "sp500":
            symbols = get_sp500_wikipedia()
        else:
            symbols = load_universe_txt(args.universe_file)
        loader = YFinanceLoader()
        price_data = loader.get(symbols, start=args.start, end=args.end)
        bench = loader.get([cfg.benchmark_symbol], start=args.start, end=args.end)
        benchmark_close = bench[cfg.benchmark_symbol]["Close"]

    print(f"Loaded {len(price_data)} symbols.")
    pf = run_backtest(price_data, benchmark_close, cfg)
    stats = summarize(pf)
    print_summary(stats)

    if pf.closed_trades:
        import pandas as pd

        trades_df = pd.DataFrame([t.__dict__ for t in pf.closed_trades])
        trades_df.to_csv(args.out, index=False)
        print(f"Wrote {len(trades_df)} trades to {args.out}")


if __name__ == "__main__":
    main()
