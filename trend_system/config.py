"""
All tunable parameters for the tail-distribution trend-following system, in one
place. The philosophy behind the defaults (see trend_system/README.md):

  - Most individual stock bets go nowhere or lose (Crittenden/Longboard,
    Bessembinder). Selection accuracy is not the edge.
  - Returns are extremely right-skewed: a small fraction of names produce
    ~all of the gain. The edge is a process that is blind to *which* stock
    wins but structurally ends up overweight it: enter many small starter
    positions, cut losers fast and mechanically, add to winners as they
    prove themselves, and let survivors run with a trailing exit instead of
    a price target.

Nothing here should be tuned by staring at a single backtest until the
equity curve looks good -- that reintroduces the hindsight bias the whole
system exists to avoid. Tune position sizing / risk knobs for your own risk
tolerance; leave the selection knobs (trend template thresholds) close to
the published defaults, since they are the part with the least room for
overfitting.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    # ---- Capital -----------------------------------------------------
    # Total account equity, and what fraction of it this system is allowed
    # to deploy. Keep the rest in a cap-weighted index fund (Longboard's
    # point: the index already runs a crude version of this same
    # cut-losers/ride-winners process for free via reconstitution).
    total_equity: float = 100_000.0
    active_sleeve_pct: float = 0.20  # fraction of total_equity this system trades

    # ---- Universe & benchmark -----------------------------------------
    benchmark_symbol: str = "SPY"

    # ---- Trend template (selection filter) -----------------------------
    # Price structure: stage-2 uptrend a la Minervini / Weinstein.
    sma_short: int = 50
    sma_mid: int = 150
    sma_long: int = 200
    sma_long_trend_lookback_days: int = 22   # SMA200 must be rising over this window
    pct_above_52wk_low_min: float = 0.30     # price >= 1.30 * 52wk low
    pct_below_52wk_high_max: float = 0.25    # price >= 0.75 * 52wk high
    rs_lookback_days: int = 252              # trailing window for relative strength
    rs_percentile_min: float = 0.70          # RS rank vs universe, cross-sectional

    # ---- Entry trigger ---------------------------------------------------
    # A trend-template pass is necessary but not sufficient; entry fires on
    # a new N-day high (proxy for "new multi-year high" behavior the study
    # found precedes the biggest winners) or a base breakout.
    breakout_lookback_days: int = 20

    # ---- Risk & position sizing ------------------------------------------
    # Risk-based sizing: never "conviction sized". Every unit risks the same
    # fraction of the sleeve at the stop, whether it's the 1st or the 30th
    # idea this month.
    risk_pct_per_unit: float = 0.0075        # 0.75% of sleeve equity per unit at stop
    initial_stop_pct: float = 0.08           # hard stop, % below entry
    atr_period: int = 20
    atr_stop_multiple: float = 2.5           # alt stop = entry - atr_stop_multiple * ATR
    use_atr_stop: bool = True                # use min(pct stop, atr stop) distance
    max_position_pct: float = 0.10           # cap any single name at 10% of sleeve
    max_open_positions: int = 20             # forces breadth across the tail
    max_portfolio_heat_pct: float = 0.08      # cap sum of open (entry-stop) risk at 8% of sleeve

    # ---- Pyramiding (the actual lever, not stock-picking accuracy) -------
    pyramid_enabled: bool = True
    max_units_per_position: int = 3
    pyramid_add_trigger_pct: float = 0.10    # add when unrealized gain since last add >= 10%
    pyramid_unit_risk_scale: float = 0.5     # each add risks 0.5x the prior unit's risk
    breakeven_after_first_add: bool = True   # ratchet stop to breakeven once 2nd unit added

    # ---- Exit / trailing stop --------------------------------------------
    trail_activate_gain_pct: float = 0.20    # switch from hard stop to trailing once +20%
    trail_pct: float = 0.18                  # trail this far below the highest close since entry
    exit_on_close_below_sma: Optional[int] = 50  # None to disable; else SMA period

    # ---- Backtest mechanics ------------------------------------------------
    commission_per_trade: float = 0.0
    slippage_pct: float = 0.001              # 10 bps assumed slippage each side
    start_date: str = "2010-01-01"
    end_date: Optional[str] = None           # None = through latest available

    def sleeve_equity(self) -> float:
        return self.total_equity * self.active_sleeve_pct
