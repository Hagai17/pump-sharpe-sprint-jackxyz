# Candidates — strategy iterations and grade results

Competition: u-jackxyz72-pump-fun-sharpe-sprint-cleanest-03e4f532
(pump.fun Sharpe sprint; ranking = hourly Sharpe of the equity curve,
marks anchored to the dataset's first block time; guardrails: ≥ 20
distinct mints with a ≥ 0.01 SOL round trip, max drawdown ≤ 50%;
prize requires net PnL > 0).

Method: an offline replica of the kit's replay engine (validated against
the kit's own `grade-local`: equal-weight baseline reproduced to
1 lamport) allowed ~40 strategy variants to be graded in seconds each.
Promising variants were re-checked on four disjoint 6-hour slices of the
dev window, because the final grade is a single fresh 6-hour window.
Headline numbers below are offline-simulator grades on the full 24 h dev
window unless marked **[grade-local]** (the kit's real grader).

## Baselines (for scale)

| strategy | net PnL | Sharpe | max DD | notes |
| --- | --- | --- | --- | --- |
| hold cash | 0 | null | 0 | no trades → fails guardrail |
| kit equal-weight example | −0.019 SOL | −0.22 | 0.9% | **[grade-local]** −0.019283154 SOL |
| kit momentum pilot (house defaults) | −9.437 SOL | — | 95.4% | **[grade-local]**; pyramiding on weak flow is catastrophic here |

## Iteration log

1. **v1 — whale/momentum + TP/SL exits** (stake 0.05, TP +10%, stop −6%,
   trailing): −0.88 SOL, Sharpe −1.49. First run also exposed a fatal
   bug: exits that hit the engine's seeded 2% tx-failure draw were never
   retried, stranding all 5 position slots by hour ~10. Fixed (retry on
   failed/rejected sell fills + a no-exit-in-flight safety reset); every
   later variant includes the fix.
2. **Exit-style sweep**: take-profits and trailing stops truncate the
   rare large winners that pay the ~2.5% round-trip curve fees; pure
   time exits beat TP/SL at every horizon tested.
3. **Signal sweep**: generic momentum and dip-buying are negative at all
   horizons net of fees. Expectancy rises monotonically with the size of
   the followed buy: ≥1.5 SOL −0.53 SOL → two ≥1.5 SOL prints −0.22 →
   ≥3 SOL −0.18 → **≥5 SOL +0.04 SOL** (stake 0.05, 300-slot hold,
   −10% stop).
4. **Dead ends, each tested and rejected**: an intra-hour profit-lock /
   loss-stop governor (truncates good regimes, cannot react inside bad
   ones), pyramiding into winners, capitulation-base entries, requiring
   coin age ≥ 1000 slots (the whale edge lives in *young* coins),
   excluding creator buys (no effect), completion-proximity baskets.
5. **Final tuning**: whale print must be *fresh* (within the last 3 tape
   entries), net 30-slot flow ≥ +0.5 SOL, ≥ 2 distinct buyers. Same
   full-window Sharpe as the unfitered version (+0.23) with a materially
   better worst 6 h slice (−0.61 vs −0.72 Sharpe; −0.025 vs −0.076 SOL).
   Stake 0.30 SOL: Sharpe is near scale-invariant here, but the fixed
   105,000-lamport per-order fee drags less at larger stakes, and max
   drawdown stays ≤ 2%.

## Final configuration (in `main.py`)

Whale ≥ 5 SOL within last 3 tape entries · net30 ≥ +0.5 SOL · ≥ 2 buyers ·
reserves 1.5–84 SOL · age ≥ 100 slots · chase guard +60%/30 slots ·
stake 0.30 SOL (1%-of-reserves cap applies) · ≤ 5 positions · ≤ 2.1 SOL
committed · hold 300 slots · stop −10% · no TP · hour-boundary guard
240 s · scout sleeve 0.011 SOL paced at 3.4 mints/hour · failed-exit
retries on.

## Final grades

Offline simulator, four 6 h slices of the dev window (final-window
proxies): PnL −0.025 / −0.050 / +0.238 / +0.108 SOL at stake 0.20;
the shipped stake-0.30 configuration: full-window +0.43 SOL.

**Kit `grade-local` (real grader, dev window, two executions):**

| metric | value |
| --- | --- |
| Sharpe | **+0.2287** |
| Net PnL | **+0.4278 SOL** |
| Max drawdown | 0.0171 |
| Win rate | 0.214 |
| Fills / round trips | 56 / 28 |
| Distinct round-tripped mints | 25 (≥ 20 ✓) |
| Rejected / failed | 0 / 2 |
| Guardrails | compliant |
| Determinism | L1 regrade reproduced byte for byte |

An earlier repo assembly that accidentally ran the research DEFAULTS
(whale ≥ 1.5 SOL, TP/SL exits, stake 0.20) graded −1.92 SOL / Sharpe
−0.91 on the same grader — recorded here as a warning: the entry point
passes its full configuration explicitly, and DEFAULTS in `main.py` are
research settings only.

Caveat (per the winners study): dev-window numbers are one sample. The
final window is a fresh 6 h of live flow; the design choices above favor
parameter plateaus and worst-slice behavior over the single best cell.
