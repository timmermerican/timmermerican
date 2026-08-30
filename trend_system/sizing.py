"""Risk-based position sizing. Every unit is sized so that a stop-out costs
the same fraction of sleeve equity, regardless of how convinced anyone is
about the name -- that's the point: uniform risk in, let the market decide
which ones get to grow via pyramiding.
"""

from __future__ import annotations

from .config import Config


def stop_distance(entry_price: float, atr_value: float, cfg: Config) -> float:
    pct_stop = entry_price * cfg.initial_stop_pct
    if not cfg.use_atr_stop or atr_value is None or atr_value != atr_value:  # NaN check
        return pct_stop
    atr_stop = cfg.atr_stop_multiple * atr_value
    return min(pct_stop, atr_stop) if atr_stop > 0 else pct_stop


def initial_unit_shares(sleeve_equity: float, entry_price: float, stop_px_distance: float,
                         cfg: Config) -> float:
    if stop_px_distance <= 0:
        return 0.0
    risk_dollars = sleeve_equity * cfg.risk_pct_per_unit
    shares = risk_dollars / stop_px_distance
    max_shares_by_cap = (sleeve_equity * cfg.max_position_pct) / entry_price
    return max(0.0, min(shares, max_shares_by_cap))


def pyramid_unit_shares(sleeve_equity: float, entry_price: float, stop_px_distance: float,
                         unit_number: int, cfg: Config) -> float:
    """unit_number is 2 for the first add, 3 for the second add, etc."""
    if stop_px_distance <= 0:
        return 0.0
    scale = cfg.pyramid_unit_risk_scale ** (unit_number - 1)
    risk_dollars = sleeve_equity * cfg.risk_pct_per_unit * scale
    return max(0.0, risk_dollars / stop_px_distance)
