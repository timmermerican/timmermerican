"""Portfolio state: positions, cash, stops, pyramids, equity tracking.

This is where the actual "let winners run, cut losers fast" mechanics live
-- the sizing math (sizing.py) decides how big a unit is; this module
tracks what happens to it over time (stop ratchets, adds, exits).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import Config


@dataclass
class Position:
    symbol: str
    shares: float = 0.0
    units: List[dict] = field(default_factory=list)  # each: {shares, entry_price, entry_date}
    stop_price: float = 0.0
    highest_close_since_entry: float = 0.0
    trailing_active: bool = False
    last_add_price: Optional[float] = None

    @property
    def avg_entry_price(self) -> float:
        if self.shares == 0:
            return 0.0
        cost = sum(u["shares"] * u["entry_price"] for u in self.units)
        return cost / self.shares

    @property
    def open_risk(self) -> float:
        """Dollar risk remaining if stopped out right now."""
        if self.shares == 0 or self.stop_price <= 0:
            return 0.0
        entry_cost = sum(u["shares"] * u["entry_price"] for u in self.units)
        stop_value = self.shares * self.stop_price
        return max(0.0, entry_cost - stop_value)


@dataclass
class Trade:
    symbol: str
    entry_date: object
    exit_date: object
    shares: float
    avg_entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    units_added: int


class Portfolio:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.cash = cfg.sleeve_equity()
        self.positions: Dict[str, Position] = {}
        self.closed_trades: List[Trade] = []
        self.equity_curve: List[dict] = []

    def sleeve_equity_now(self, prices: Dict[str, float]) -> float:
        eq = self.cash
        for sym, pos in self.positions.items():
            px = prices.get(sym)
            if px is not None:
                eq += pos.shares * px
        return eq

    def total_open_risk(self) -> float:
        return sum(p.open_risk for p in self.positions.values())

    def open_position(self, symbol: str, shares: float, price: float, stop_price: float, date):
        cost = shares * price * (1 + self.cfg.slippage_pct) + self.cfg.commission_per_trade
        if cost > self.cash:
            shares = self.cash / (price * (1 + self.cfg.slippage_pct))
            cost = shares * price * (1 + self.cfg.slippage_pct)
        if shares <= 0:
            return
        self.cash -= cost
        pos = Position(symbol=symbol)
        pos.shares = shares
        pos.units.append({"shares": shares, "entry_price": price, "entry_date": date})
        pos.stop_price = stop_price
        pos.highest_close_since_entry = price
        pos.last_add_price = price
        self.positions[symbol] = pos

    def add_unit(self, symbol: str, shares: float, price: float, date):
        pos = self.positions[symbol]
        cost = shares * price * (1 + self.cfg.slippage_pct) + self.cfg.commission_per_trade
        if cost > self.cash:
            shares = max(0.0, self.cash / (price * (1 + self.cfg.slippage_pct)))
            cost = shares * price * (1 + self.cfg.slippage_pct)
        if shares <= 0:
            return
        self.cash -= cost
        pos.units.append({"shares": shares, "entry_price": price, "entry_date": date})
        pos.shares += shares
        pos.last_add_price = price
        if self.cfg.breakeven_after_first_add and len(pos.units) == 2:
            pos.stop_price = max(pos.stop_price, pos.avg_entry_price)

    def close_position(self, symbol: str, price: float, date):
        pos = self.positions.pop(symbol)
        proceeds = pos.shares * price * (1 - self.cfg.slippage_pct) - self.cfg.commission_per_trade
        self.cash += proceeds
        avg_entry = pos.avg_entry_price
        pnl = proceeds - sum(u["shares"] * u["entry_price"] for u in pos.units)
        pnl_pct = (price / avg_entry - 1.0) if avg_entry else 0.0
        self.closed_trades.append(
            Trade(
                symbol=symbol,
                entry_date=pos.units[0]["entry_date"],
                exit_date=date,
                shares=pos.shares,
                avg_entry_price=avg_entry,
                exit_price=price,
                pnl=pnl,
                pnl_pct=pnl_pct,
                units_added=len(pos.units),
            )
        )

    def record_equity(self, date, prices: Dict[str, float]):
        self.equity_curve.append(
            {"date": date, "equity": self.sleeve_equity_now(prices), "cash": self.cash,
             "n_positions": len(self.positions)}
        )
