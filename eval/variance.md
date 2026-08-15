# Variance Analysis

Model: `gpt-4o-mini`  |  Runs per level: 5  |  Temperature: 0.7

HCPC F1 across independent runs (caching disabled).

| Level | Mean F1 | Std Dev | Min | Max |
|---|---|---|---|---|
| L0 | 0.486 | 0.091 | 0.333 | 0.556 |
| L1 | 0.622 | 0.054 | 0.556 | 0.667 |
| L2 | 0.565 | 0.057 | 0.489 | 0.667 |
| L3 | 0.422 | 0.044 | 0.389 | 0.500 |
| L4 | 0.622 | 0.054 | 0.556 | 0.667 |

## Reading this table

- A low standard deviation means the level's performance is stable and the headline F1 is trustworthy, not a lucky single run.
- A high standard deviation means the level is sensitive to sampling and its point estimate should be treated with caution.
- Structured levels (L1+) are expected to be more stable than the free-form baseline (L0), since a fixed schema removes one source of run-to-run variation.