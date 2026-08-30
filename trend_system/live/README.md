# Path to full automation

The backtest engine (`trend_system/backtest.py`) and the live trader you'd
eventually run share the same core logic (`screener.py`, `sizing.py`,
`portfolio.py`) -- that's intentional, so "what the backtest says" and
"what the bot does" can't silently diverge. What's missing to go live:

1. **Broker adapter.** Implement a class with:
   - `get_positions() -> dict[symbol, shares]`
   - `get_account_equity() -> float`
   - `place_order(symbol, shares, side, order_type="market")`
   - `get_bars(symbols, lookback_days) -> dict[symbol, DataFrame]` (or pull
     from a market-data API separately)

   Alpaca (commission-free, simple REST API, has a paper-trading
   environment) is the easiest starting point. Interactive Brokers (`ib_insync`)
   is the more institutional-grade option if you want the API to also cover
   the passive 80% sleeve.

2. **Same-bar lookahead fix.** The backtest evaluates signals and executes
   at the same day's close for simplicity. A live bot can't do that --
   you don't know the close is the close until the market closes. Two
   honest options:
   - Run the screener after close using that day's final bar, and submit
     orders to execute at tomorrow's open (re-run the backtest with a
     1-day execution lag and confirm the edge survives -- it should
     shrink, since new-high breakouts often gap on the open, but a
     structurally sound system doesn't live or die on same-bar fills).
   - Run intraday against a live/delayed feed and trigger on an
     intraday breakout through the trend-template levels.

   Do not deploy capital against the same-bar backtest results as-is.

3. **Daily job.** A scheduled script (cron, or a small always-on process)
   that: pulls fresh bars -> recomputes indicators -> runs the screener ->
   diffs against current live positions -> computes stop/pyramid/entry
   orders -> submits them -> logs everything. Given you said daily
   monitoring is realistic for you, this can run once after close and you
   confirm the order batch before it fires, rather than firing unattended
   -- much lower operational risk while you build trust in the system.

4. **Reconciliation.** Live fills will never exactly match backtest
   assumptions (slippage, partial fills, gaps through stops). Log every
   live trade and periodically compare realized slippage to the
   `slippage_pct` assumption in `Config`; tighten position sizing if
   real slippage is materially worse (illiquid small caps will be).

None of this is wired up yet -- say the word (and which broker) and I'll
build the adapter and the daily job next.
