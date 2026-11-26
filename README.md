# pump-sharpe-sprint-jackxyz

A deterministic pump.fun replay bot for the make.fun **Sharpe sprint**
(u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532) — ranked by the
hourly Sharpe ratio of its equity curve, so the design goal is *steady,
low-variance* gains rather than maximum return.

[![Competition](https://make.fun/badge/u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532.svg)](https://make.fun/c/u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532?utm_source=github&utm_medium=referral&utm_campaign=u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532)
[![Rank](https://make.fun/badge/u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532/Hagai17/pump-sharpe-sprint-jackxyz/rank.svg)](https://make.fun/c/u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532?utm_source=github&utm_medium=referral&utm_campaign=u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532)
[![Reward](https://make.fun/badge/u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532/Hagai17/pump-sharpe-sprint-jackxyz/reward.svg)](https://make.fun/c/u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532?utm_source=github&utm_medium=referral&utm_campaign=u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532)

Competition: https://make.fun/c/u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532?utm_source=github&utm_medium=referral&utm_campaign=u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532

## Strategy — "steady whale-follow"

Two sleeves over the bonding-curve replay (10 SOL start, ≤5 open positions,
orders capped at 1% of a curve's real quote reserves, 2-slot fill latency,
~2.5% round-trip curve fees):

* **Whale sleeve (profit engine).** Enter only on the largest prints: a
  single recorded buy of **≥ 5 SOL** within the last 3 tape entries, net
  30-slot flow ≥ **+0.5 SOL**, and ≥ 2 distinct buyers — on curves with
  1.5–84 SOL of real reserves, at least 100 slots old, and not already up
  more than 60% in 30 slots. Stake 0.30 SOL (shrunk by the size cap),
  ≤ 2.1 SOL committed in total. Exit on a **fixed 300-slot hold** or a
  **−10% liquidation-value stop**, whichever comes first. No profit target:
  in every local test, targets truncated the rare large winners that pay
  for the curve fees, while the fixed hold keeps hourly returns even.
* **Scout sleeve (guardrail insurance).** Minimum-size 0.011 SOL round
  trips on the most liquid curves, sold as soon as the buy lands, paced at
  ~3.4 fresh mints per elapsed hour. This guarantees the ≥ 20 distinct
  round-tripped mints guardrail at a small, evenly spread cost instead of
  a deadline scramble.

Risk shaping for Sharpe: no new entries in the last 240 s of an hour
bucket (a late-hour loss lands entirely in one hourly return), and exits
that come back `failed`/`rejected` are retried on the next batch — the
engine's seeded 2% transaction-failure draw otherwise strands positions
in their slots forever.

The bot is fully deterministic: no wall clock, no environment reads, no
unseeded randomness. Hour buckets derive from event timestamps only, so
two executions produce byte-identical action logs.

## Files

* `main.py` — the bot (strategy + entry point).
* `vendor/makefun_bot.py` — the make.fun Python SDK, vendored from the
  competition kit.
* `candidates.md` — strategy iterations and local grade results.

## Dev-window grade (kit `grade-local`, frozen `src-calib` dataset)

| metric | value |
| --- | --- |
| Sharpe (hourly, non-annualized) | **+0.229** |
| Net PnL | **+0.428 SOL** |
| Max drawdown | 1.71% (limit 50%) |
| Fills / round trips | 56 / 28 |
| Distinct round-tripped mints | 25 (guardrail ≥ 20) |
| Determinism | two executions, action logs byte-identical |

## License

MIT — see `LICENSE`.
