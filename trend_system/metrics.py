"""Summary statistics for a completed backtest."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .portfolio import Portfolio


def summarize(pf: Portfolio) -> dict:
    curve = pd.DataFrame(pf.equity_curve).set_index("date")
    if curve.empty:
        return {}

    eq = curve["equity"]
    daily_ret = eq.pct_change().dropna()

    n_years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / n_years) - 1 if n_years > 0 else float("nan")

    running_max = eq.cummax()
    drawdown = eq / running_max - 1.0
    max_dd = drawdown.min()

    sharpe = (
        daily_ret.mean() / daily_ret.std() * np.sqrt(252)
        if daily_ret.std() > 0
        else float("nan")
    )

    trades = pd.DataFrame([t.__dict__ for t in pf.closed_trades])
    if not trades.empty:
        wins = trades[trades["pnl"] > 0]
        losses = trades[trades["pnl"] <= 0]
        win_rate = len(wins) / len(trades)
        avg_win = wins["pnl_pct"].mean() if not wins.empty else 0.0
        avg_loss = losses["pnl_pct"].mean() if not losses.empty else 0.0
        profit_factor = (
            wins["pnl"].sum() / abs(losses["pnl"].sum()) if losses["pnl"].sum() != 0 else float("inf")
        )
        expectancy = trades["pnl_pct"].mean()
        avg_units = trades["units_added"].mean()
    else:
        win_rate = avg_win = avg_loss = profit_factor = expectancy = avg_units = float("nan")

    return {
        "start_equity": float(eq.iloc[0]),
        "end_equity": float(eq.iloc[-1]),
        "cagr": cagr,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "n_trades": len(trades),
        "win_rate": win_rate,
        "avg_win_pct": avg_win,
        "avg_loss_pct": avg_loss,
        "profit_factor": profit_factor,
        "expectancy_pct_per_trade": expectancy,
        "avg_units_per_trade": avg_units,
    }


def print_summary(stats: dict) -> None:
    if not stats:
        print("No equity curve to summarize.")
        return
    print("=" * 50)
    print(f"Start equity:        ${stats['start_equity']:,.0f}")
    print(f"End equity:          ${stats['end_equity']:,.0f}")
    print(f"CAGR:                {stats['cagr']:.2%}")
    print(f"Max drawdown:        {stats['max_drawdown']:.2%}")
    print(f"Sharpe (naive):      {stats['sharpe']:.2f}")
    print(f"Trades:              {stats['n_trades']}")
    print(f"Win rate:            {stats['win_rate']:.2%}")
    print(f"Avg win:             {stats['avg_win_pct']:.2%}")
    print(f"Avg loss:            {stats['avg_loss_pct']:.2%}")
    print(f"Profit factor:       {stats['profit_factor']:.2f}")
    print(f"Expectancy/trade:    {stats['expectancy_pct_per_trade']:.2%}")
    print(f"Avg units/trade:     {stats['avg_units_per_trade']:.2f}")
    print("=" * 50)
