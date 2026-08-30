"""Event-driven daily backtest loop.

Order of operations each day (all decisions use data through end-of-day t,
executed at t's close -- a simplification; a live/paper version would
execute at t+1's open to avoid same-bar lookahead, see live/README.md):

  1. Update trailing stops / stop ratchets on existing positions.
  2. Check exits (stop hit, or trend-break exit) -- process before entries
     so a stopped-out slot can free capital/heat budget for the same day.
  3. Check pyramid adds on existing winners.
  4. Check new entries: trend template + breakout, respecting max
     positions and max portfolio heat.
  5. Record equity.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import pandas as pd

from . import screener as scr
from .config import Config
from .portfolio import Portfolio
from .sizing import initial_unit_shares, pyramid_unit_shares, stop_distance


def run_backtest(price_data: Dict[str, pd.DataFrame], benchmark_close: pd.Series, cfg: Config):
    features = {
        sym: scr.compute_features(df, benchmark_close, cfg) for sym, df in price_data.items()
    }

    all_dates = sorted(set().union(*[df.index for df in price_data.values()]))
    all_dates = [d for d in all_dates if d >= pd.Timestamp(cfg.start_date)]
    if cfg.end_date:
        all_dates = [d for d in all_dates if d <= pd.Timestamp(cfg.end_date)]

    idx_of: Dict[str, Dict[pd.Timestamp, int]] = {
        sym: {d: i for i, d in enumerate(df.index)} for sym, df in price_data.items()
    }

    pf = Portfolio(cfg)

    for date in all_dates:
        prices_today: Dict[str, float] = {}
        rs_today: Dict[str, float] = {}
        eligible: List[str] = []

        for sym, feat in features.items():
            i = idx_of[sym].get(date)
            if i is None:
                continue
            prices_today[sym] = float(feat.close.iloc[i])
            rs_today[sym] = float(feat.rs.iloc[i]) if not pd.isna(feat.rs.iloc[i]) else float("nan")

        rs_ranks = scr.rs_percentile_rank(rs_today)

        # 1 & 2: manage existing positions (stops, trend-break, trailing)
        for sym in list(pf.positions.keys()):
            i = idx_of.get(sym, {}).get(date)
            if i is None:
                continue
            feat = features[sym]
            close = prices_today[sym]
            pos = pf.positions[sym]

            pos.highest_close_since_entry = max(pos.highest_close_since_entry, close)
            gain_pct = close / pos.avg_entry_price - 1.0 if pos.avg_entry_price else 0.0

            if gain_pct >= cfg.trail_activate_gain_pct:
                pos.trailing_active = True
            if pos.trailing_active:
                trail_stop = pos.highest_close_since_entry * (1 - cfg.trail_pct)
                pos.stop_price = max(pos.stop_price, trail_stop)

            trend_break = False
            if cfg.exit_on_close_below_sma == cfg.sma_short:
                sma_val = feat.sma_short.iloc[i]
                trend_break = not pd.isna(sma_val) and close < sma_val

            if close <= pos.stop_price or trend_break:
                pf.close_position(sym, close, date)
                continue

        # 3: pyramid adds
        if cfg.pyramid_enabled:
            for sym in list(pf.positions.keys()):
                i = idx_of.get(sym, {}).get(date)
                if i is None:
                    continue
                pos = pf.positions[sym]
                if len(pos.units) >= cfg.max_units_per_position:
                    continue
                close = prices_today[sym]
                if pos.last_add_price is None:
                    continue
                gain_since_add = close / pos.last_add_price - 1.0
                if gain_since_add < cfg.pyramid_add_trigger_pct:
                    continue
                feat = features[sym]
                atr_val = feat.atr.iloc[i]
                dist = stop_distance(close, atr_val, cfg)
                sleeve_eq = pf.sleeve_equity_now(prices_today)
                add_shares = pyramid_unit_shares(sleeve_eq, close, dist, len(pos.units) + 1, cfg)
                if add_shares <= 0:
                    continue
                if pf.total_open_risk() + add_shares * dist > sleeve_eq * cfg.max_portfolio_heat_pct:
                    continue
                pf.add_unit(sym, add_shares, close, date)

        # 4: new entries
        if len(pf.positions) < cfg.max_open_positions:
            for sym, feat in features.items():
                if sym in pf.positions:
                    continue
                i = idx_of[sym].get(date)
                if i is None or i < 1:
                    continue
                if not scr.trend_template_pass(feat, i, cfg):
                    continue
                if rs_ranks.get(sym, 0.0) < cfg.rs_percentile_min:
                    continue
                if not scr.breakout_trigger(feat, i):
                    continue

                close = prices_today[sym]
                atr_val = feat.atr.iloc[i]
                dist = stop_distance(close, atr_val, cfg)
                if dist <= 0:
                    continue
                sleeve_eq = pf.sleeve_equity_now(prices_today)
                shares = initial_unit_shares(sleeve_eq, close, dist, cfg)
                if shares <= 0:
                    continue
                if pf.total_open_risk() + shares * dist > sleeve_eq * cfg.max_portfolio_heat_pct:
                    continue

                stop_price = close - dist
                pf.open_position(sym, shares, close, stop_price, date)

                if len(pf.positions) >= cfg.max_open_positions:
                    break

        pf.record_equity(date, prices_today)

    return pf
