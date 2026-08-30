# Tail-distribution trend-following system

A rule-based system built on one premise: you can't identify the next
Microsoft in advance, but you don't need to. Longboard's "Capitalism
Distribution" study and Bessembinder's 2018 paper both show the same
shape -- most stocks lose or go nowhere, a small minority produce
essentially all of the market's net gain, and the winners tell on
themselves in real time by spending disproportionate time making new
highs on the way up. This system is built to be blind to *which* stock
wins and structurally end up overweight it anyway.

## The rules, precisely

**Selection (trend template).** A stock is *eligible* only if, on a given
day:
- Close > SMA50 > SMA150 > SMA200
- SMA200 has been rising for at least 22 trading days
- Close is at least 30% above its 52-week low
- Close is within 25% of its 52-week high
- Relative strength (trailing 252-day return vs. SPY) ranks in the top
  30% of the universe that day

**Entry trigger.** Eligible names only: buy on a new 20-day closing high
(the live, non-hindsight analogue of "winners make new highs
disproportionately often").

**Sizing.** Every unit risks the same 0.75% of *sleeve* equity (not total
account equity) at its stop, sized off `min(8% below entry, 2.5x ATR(20))`
stop distance. No unit can exceed 10% of sleeve equity regardless of stop
distance. No new entry is taken if it would push total open risk across
the whole book above 8% of sleeve equity, and the book is capped at 20
concurrent names -- both caps exist to force breadth, since you don't know
in advance which handful will be the tail winners.

**Pyramiding.** Once a position gains 10% since its last add, add a second
(then third) unit, each sized at half the risk of the prior unit, up to 3
units total. After the second unit, the stop ratchets to breakeven --
once a name has proven itself once, the original risk comes off the
table permanently.

**Exit.** Hard stop until the position is up 20%, then a trailing stop 18%
below the highest close since entry. No price target, ever -- the whole
point is letting the 6% of names that beat the index by >500% actually
get there.

## Why the active sleeve is capped at 20% of total capital

This is a low-hit-rate system (expect a win rate well under 50% -- the
synthetic smoke test below came in around 18-25% depending on regime,
which is directionally consistent with a right-skewed-return system: most
trades are small, mechanically-cut losses, a few are large winners that
carry the book). That's fine *if* the position sizing is disciplined and
you're not relying on it for near-term cash needs, but it's the wrong
place for capital you can't watch trend through a real drawdown. The
other 80% sitting in a cap-weighted index fund is not a hedge or an
afterthought -- Longboard's own point is that cap-weighting is already a
crude, free version of this same cut-losers/ride-winners process via
index reconstitution. The active sleeve is only worth running if you
genuinely execute the stop discipline better than that free baseline;
otherwise you're adding operational risk for no edge.

## Honest caveats before you trust a backtest number

1. **Survivorship bias.** `universe.get_sp500_wikipedia()` returns
   *today's* constituents. Backtesting against that universe silently
   deletes every name that got delisted or kicked out of the index for
   collapsing -- exactly the ~39%-lost-money cohort this system is
   designed to avoid getting run over by. A real historical backtest
   needs a point-in-time, delisting-inclusive universe (Sharadar/Nasdaq
   Data Link's SEP+SF1 bundle or Norgate Data are the standard sources).
   Treat any backtest run against the Wikipedia list as directionally
   informative at best, not a real performance estimate.
2. **Same-bar execution.** The backtest signals and fills on the same
   day's close, which isn't achievable live. See `live/README.md` before
   deploying capital.
3. **No shorting, no sector/market regime filter.** The system is long-only
   and fully committed to individual-name trend signals regardless of
   overall market regime (e.g. it will keep trying to enter breakouts
   during a broad bear market, where breakout failure rates are much
   higher). Adding a simple market-trend filter (e.g. only take new
   entries while SPY is above its 200-day SMA) is a reasonable next
   iteration once you've run this and want to reduce whipsaw.
4. **Every threshold in `config.py` is a real decision, not a magic
   number.** They're set close to the published Minervini/Turtle
   defaults deliberately -- resist the urge to optimize them against a
   backtest until they look best; that's how you re-inject the hindsight
   bias this whole approach exists to avoid. If you want to adjust
   anything, adjust the risk knobs (risk_pct_per_unit, max_position_pct,
   active_sleeve_pct) for your own risk tolerance, not the selection
   thresholds.

## Running it

```
pip install -r requirements.txt

# Real backtest (needs network access to Yahoo Finance + Wikipedia;
# survivorship-biased universe, see caveat #1 above)
python run_backtest.py --universe sp500 --start 2015-01-01 --equity 100000 --sleeve-pct 0.20

# Offline smoke test of the engine itself, no network, synthetic data
python run_backtest.py --synthetic --n-symbols 80 --start 2015-01-01 --end 2023-12-31
```

Outputs a summary (CAGR, max drawdown, win rate, profit factor,
expectancy, avg units per trade) and a CSV of every closed trade.

## Next steps

- Point it at a real point-in-time universe and re-run before drawing any
  conclusions from performance numbers.
- Decide on a broker for live execution -- see `live/README.md`.
- Once you've watched it run on paper for a while, decide whether 20%
  active / 80% passive is still the right split for you.
