# Untouched GBPUSD Market-Mechanics Test — Result

**Run:** GitHub Actions `33946974582`  
**Frozen-test commit executed:** `b0fd2361e70f8adf1708da931a6a9fc8f350893d`  
**Date executed:** 2026-09-05  
**Verdict:** `PREDICTIVE_INFORMATION_NOT_ESTABLISHED`

## Objective

Test whether the predeclared GBPUSD market-mechanics sequence contains statistically meaningful directional information on historical data that predates the ATHENA development/backtest period.

The tested sequence was frozen before the valid holdout was processed:

1. Confirmed 3-left / 3-right swing.
2. Price sweeps the confirmed swing by at least 0.10 ATR and closes back inside it.
3. Within 6 H1 bars, a displacement candle forms in the rejection direction with body at least 1.8 times the prior-20-bar mean body.
4. The displacement candle must close beyond the raid-bar extreme in the rejection direction.
5. Signals are separated by a 24-bar cooldown.
6. Primary outcome: direction-adjusted close-to-close return after 12 H1 bars is positive.

No search over those parameters was permitted on the holdout.

## Source and integrity

Provider: FXCM public H1 candle archive.  
Instrument: GBPUSD.  
Source-restricted untouched holdout: 2012-01-01 through 2019-12-31 UTC.  
420 weekly source files were fetched.  
Aggregate source SHA-256: `7b62c4372a6f9abdde4cde2813efa1b592d95753daea22ad1a309d7e9721590c`.

Data accepted after validation:

- 49,532 holdout H1 rows.
- Observed holdout coverage: 2012-01-02 03:00 UTC through 2019-12-27 21:00 UTC.
- Duplicate timestamps after deduplication: 0.
- Null OHLC values: 0.
- OHLC consistency violations: 0.
- Median close spread in source: 0.00005 (0.5 pip on GBPUSD).

The first attempted external source was rejected before statistical testing because it contained zero rows in the declared 2002-2019 holdout despite its repository description. It generated no research result. The valid FXCM source documents coverage from 2012; therefore only the source boundary was changed to 2012-2019. Signal logic, thresholds, endpoint, alpha, and effect-size gate were not changed.

## Predeclared decision gate

Predictive information would be supported only if all of the following passed:

- At least 100 confirmed signals.
- Directional hit rate at least 55% (>=5 percentage-point lift over the 50% null).
- 95% Wilson confidence interval lower bound above 50%.
- One-sided exact binomial p < 0.05.
- Each fixed subperiod had at least 20 signals and a hit rate above 50%.

## Primary result

Confirmed full-mechanics signals: **878**.  
Wins: **425**.  
Directional hit rate: **48.4055%**.  
Lift versus 50% null: **-1.5945 percentage points**.  
95% Wilson interval: **45.1140% to 51.7108%**.  
One-sided exact-binomial p-value for hit rate >50%: **0.836136**.  
Mean direction-adjusted 12-hour return: **-0.09845 ATR**.  
Median direction-adjusted 12-hour return: **-0.09131 ATR**.  
One-sided t-test p-value for mean signed return >0: **0.817014**.  
1-ATR target-before-1-ATR stop hit rate within 12 bars: **43.8497%**.

**Primary conclusion:** the frozen mechanic did not outperform the 50% directional null and did not satisfy the predeclared minimum effect size or statistical-significance gates.

## Controls

### Liquidity raid without displacement/structure confirmation

Raid-only observations: **4,800**.  
Directional hit rate: **49.4792%**.  
95% Wilson interval: **48.0657% to 50.8934%**.  
One-sided p-value: **0.769170**.  
Mean direction-adjusted 12-hour return: **-0.02194 ATR**.

The raid itself showed no statistically meaningful directional edge.

### Matched nonsignal control

Matched observations: **875**.  
Full-mechanics signal hit rate: **48.4571%**.  
Matched control hit rate: **50.1714%**.  
Signal-minus-control hit-rate difference: **-1.7143 percentage points**.  
Signal mean signed return: **-0.09015 ATR**.  
Control mean signed return: **+0.07933 ATR**.  
Mean paired difference: **-0.16948 ATR**.  
One-sided paired t-test p-value for signal superiority: **0.844298**.

The full mechanic did not add positive directional information versus matched nonsignal observations; in this test it was directionally worse.

## Long / short split

Long signals: **446**, hit rate **50.4484%**, 95% Wilson interval **45.8243% to 55.0649%**, one-sided p **0.443529**, mean signed return **-0.05623 ATR**.

Short signals: **432**, hit rate **46.2963%**, 95% Wilson interval **41.6476% to 51.0103%**, one-sided p **0.943877**, mean signed return **-0.14203 ATR**.

Neither side establishes predictive information. Shorts were materially weaker in point estimate.

## Stability by untouched subperiod

- 2012-2014: n=335, hit rate **47.7612%**, mean signed return **-0.20705 ATR**.
- 2015-2017: n=329, hit rate **49.2401%**, mean signed return **+0.05565 ATR**.
- 2018-2019: n=214, hit rate **48.1308%**, mean signed return **-0.16534 ATR**.

No fixed subperiod exceeded a 50% directional hit rate. The consistency gate therefore failed independently of the aggregate result.

## Decision

The tested statement is rejected as an evidence claim:

> A confirmed swing liquidity raid, followed within six hours by the specified displacement/structure confirmation, contains statistically meaningful 12-hour directional predictive information in GBPUSD H1 data.

The evidence does **not** support that statement on the 2012-2019 untouched FXCM holdout.

This does not prove that every possible market-structure concept is useless, nor does it test the user's discretionary visual interpretation of “powerful” HTF swings. It proves only that this explicit, machine-testable formulation failed its preregistered test.

## Governance consequence

Do not tune this same formulation against the 2012-2019 holdout and then present the revised result as untouched evidence. This holdout is now spent. Any revised mechanic is a new hypothesis and requires a different untouched validation set (for example a separately preregistered instrument or genuinely future data) after development/calibration on non-holdout data.
