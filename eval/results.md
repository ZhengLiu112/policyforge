# Evaluation Results

Model: `gpt-4o-mini`  |  Articles: 3  |  Total cost: $0.0374

## Ablation table (averaged across eval set)

| Level | HCPC P | HCPC R | HCPC F1 | ICD-10 F1 | Halluc rate | Cost USD |
|---|---|---|---|---|---|---|
| L0 | 0.5 | 0.667 | 0.556 | 0.0 | 0.667 | 0.00100 |
| L1 | 0.5 | 0.667 | 0.556 | 0.0 | 0.0 | 0.00200 |
| L2 | 0.5 | 0.667 | 0.556 | 0.0 | 0.0 | 0.00300 |
| L3 | 0.5 | 0.5 | 0.445 | 0.0 | 0.0 | 0.00300 |
| L4 | 0.667 | 0.667 | 0.667 | 0.0 | 0.0 | 0.00400 |

## Per-article breakdown

| Article | Level | HCPC F1 | ICD-10 F1 | Halluc | Rules |
|---|---|---|---|---|---|
| 57237 | L0 | 1.0 | 0.0 | 0.0 | 1 |
| 57149 | L0 | 0.667 | 0.0 | 1.0 | 3 |
| 60274 | L0 | 0.0 | 0.0 | 1.0 | 5 |
| 57237 | L1 | 1.0 | 0.0 | 0.0 | 3 |
| 57149 | L1 | 0.667 | 0.0 | 0.0 | 6 |
| 60274 | L1 | 0.0 | 0.0 | 0.0 | 4 |
| 57237 | L2 | 1.0 | 0.0 | 0.0 | 3 |
| 57149 | L2 | 0.667 | 0.0 | 0.0 | 6 |
| 60274 | L2 | 0.0 | 0.0 | 0.0 | 4 |
| 57237 | L3 | 0.667 | 0.0 | 0.0 | 2 |
| 57149 | L3 | 0.667 | 0.0 | 0.0 | 5 |
| 60274 | L3 | 0.0 | 0.0 | 0.0 | 0 |
| 57237 | L4 | 1.0 | 0.0 | 0.0 | 3 |
| 57149 | L4 | 1.0 | 0.0 | 0.0 | 4 |
| 60274 | L4 | 0.0 | 0.0 | 0.0 | 14 |

## Limitations

- Sample size is small (3 articles, ~30 rules total). Results are directionally useful but should not be taken as population-level estimates.
- Ground truth is the official MCD code table, which may include codes not explicitly mentioned in the narrative (e.g. inherited from a parent LCD).
- ICD-10 F1 is less meaningful for articles with zero covered codes.
- Cost figures assume no cache hits; cached reruns cost $0.
- No cross-model comparison or variance across multiple sampling runs.