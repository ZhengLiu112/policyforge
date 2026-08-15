"""Evaluation: how well does each extraction level recover the official
code tables from article narrative text?

Ground truth: the code tables CMS published alongside each article.
Model input:  the cleaned narrative only — codes are withheld.

Metrics per article, aggregated across the eval set:
  precision  = |predicted ∩ gold| / |predicted|
  recall     = |predicted ∩ gold| / |gold|
  f1         = harmonic mean
  hallucination_rate = fraction of extracted rules whose quoted_span
                       is not found verbatim in the source text
                       (computed by validate.py, requires no annotation)
  cost_usd   = actual spend from the OpenAI usage object
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# make sure project root is on the path when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from src.extract import Level, extract_rules
from src.llm import LLMClient, Usage
from src.mcd import Article, load_articles, pick_eval_articles
from src.validate import summarize

LEVELS: list[Level] = ["L0", "L1", "L2", "L3", "L4"]
POLICIES = Path("data/policies")
RESULTS_PATH = Path("eval/results.md")
GOLD_PATH = Path("eval/gold")


# --------------------------------------------------------------------------
# Code extraction from model output
# --------------------------------------------------------------------------

_CPT_HCPCS_RE = re.compile(r"\b(?:\d{5}|\d{4}[A-Z]|[A-Z]\d{4})\b")
_ICD10_RE = re.compile(r"\b[A-TV-Z]\d[0-9A-Z](?:\.?[0-9A-Z]{1,4})?\b")


def codes_from_rules(rules) -> set[str]:
    """Collect all CPT/HCPCS codes the model produced for one article."""
    codes: set[str] = set()
    for r in rules:
        codes.update(c.upper() for c in r.trigger_codes)
        codes.update(c.upper() for c in r.conflicting_codes)
    return codes


def icd10_from_rules(rules) -> set[str]:
    """Collect ICD-10 codes from both the structured fields and any codes
    the model embedded in free-text reason strings.

    Articles list ICD-10 codes as coverage conditions, not as
    trigger/conflicting pairs, so the structured fields alone miss them.
    Scanning the reason text recovers codes the model mentioned but
    didn't route into covered_icd10.
    """
    codes: set[str] = set()
    for r in rules:
        codes.update(c.upper() for c in r.covered_icd10)
        # also scan human_readable_reason for any ICD-10 patterns
        found = _ICD10_RE.findall(r.human_readable_reason)
        codes.update(c.upper() for c in found)
    return codes


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def prf(predicted: set[str], gold: set[str]) -> dict[str, float]:
    if not predicted and not gold:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not predicted:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    if not gold:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    tp = len(predicted & gold)
    p = tp / len(predicted)
    r = tp / len(gold)
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3)}


@dataclass
class ArticleResult:
    article_id: str
    level: Level
    gold_hcpc: set[str]
    pred_hcpc: set[str]
    gold_icd10: set[str]
    pred_icd10: set[str]
    hallucination_rate: float
    abstain_rate: float
    n_rules: int
    cost_usd: float

    @property
    def hcpc_scores(self) -> dict:
        return prf(self.pred_hcpc, self.gold_hcpc)

    @property
    def icd10_scores(self) -> dict:
        return prf(self.pred_icd10, self.gold_icd10)

    def row(self) -> dict:
        return {
            "article_id": self.article_id,
            "level": self.level,
            "hcpc_p": self.hcpc_scores["precision"],
            "hcpc_r": self.hcpc_scores["recall"],
            "hcpc_f1": self.hcpc_scores["f1"],
            "icd10_p": self.icd10_scores["precision"],
            "icd10_r": self.icd10_scores["recall"],
            "icd10_f1": self.icd10_scores["f1"],
            "hallucination_rate": round(self.hallucination_rate, 3),
            "abstain_rate": round(self.abstain_rate, 3),
            "n_rules": self.n_rules,
            "cost_usd": round(self.cost_usd, 5),
        }


# --------------------------------------------------------------------------
# Main evaluation loop
# --------------------------------------------------------------------------

def evaluate_article(
    client: LLMClient,
    article: Article,
    level: Level,
) -> ArticleResult:
    outcome = extract_rules(
        client,
        level=level,
        doc_id=article.article_id,
        doc_title=article.title,
        doc_version=article.version,
        policy_text=article.narrative,
    )

    val_summary = summarize(outcome.reports)
    pred_hcpc = codes_from_rules(outcome.rules)
    pred_icd10 = icd10_from_rules(outcome.rules)

    return ArticleResult(
        article_id=article.article_id,
        level=level,
        gold_hcpc=article.hcpc_codes,
        pred_hcpc=pred_hcpc,
        gold_icd10=article.icd10_covered,
        pred_icd10=pred_icd10,
        hallucination_rate=val_summary["hallucination_rate"],
        abstain_rate=val_summary["needs_review_rate"],
        n_rules=len(outcome.rules),
        cost_usd=outcome.usage.get("cost_usd", 0.0),
    )


def run(model: str = "gpt-4o-mini", n_articles: int = 3) -> None:
    print(f"Loading articles from {POLICIES} ...")
    articles = load_articles(POLICIES)
    eval_set = pick_eval_articles(articles, n=n_articles)

    if not eval_set:
        print("ERROR: no articles selected — check pick_eval_articles thresholds")
        sys.exit(1)

    print(f"Eval set: {len(eval_set)} articles")
    for a in eval_set:
        print(f"  [{a.article_id}] {a.title[:65]}  hcpc={len(a.hcpc_codes)} icd10={len(a.icd10_covered)}")

    GOLD_PATH.mkdir(parents=True, exist_ok=True)
    for a in eval_set:
        (GOLD_PATH / f"{a.article_id}.json").write_text(
            json.dumps({
                "article_id": a.article_id,
                "title": a.title,
                "hcpc_codes": sorted(a.hcpc_codes),
                "icd10_covered": sorted(a.icd10_covered),
                "icd10_noncovered": sorted(a.icd10_noncovered),
                "narrative_chars": len(a.narrative),
            }, indent=2),
            encoding="utf-8",
        )

    client = LLMClient(model=model, use_cache=True)
    all_rows: list[dict] = []
    total_cost = 0.0

    for level in LEVELS:
        print(f"\n--- {level} ---")
        level_cost = 0.0
        for article in eval_set:
            result = evaluate_article(client, article, level)
            row = result.row()
            all_rows.append(row)
            level_cost += row["cost_usd"]
            print(f"  {article.article_id:6s}  "
                  f"hcpc F1={row['hcpc_f1']:.2f}  "
                  f"icd10 F1={row['icd10_f1']:.2f}  "
                  f"halluc={row['hallucination_rate']:.2f}  "
                  f"rules={row['n_rules']}")
        total_cost += level_cost
        print(f"  level cost: ${level_cost:.5f}")

    print(f"\nTotal cost: ${total_cost:.4f}")
    write_results(all_rows, eval_set, model, total_cost)
    print(f"\nResults written to {RESULTS_PATH}")


def _agg(rows: list[dict], level: str, col: str) -> float:
    vals = [r[col] for r in rows if r["level"] == level]
    return round(sum(vals) / len(vals), 3) if vals else 0.0


def write_results(rows: list[dict], eval_set: list[Article],
                  model: str, total_cost: float) -> None:
    lines = [
        "# Evaluation Results",
        "",
        f"Model: `{model}`  |  Articles: {len(eval_set)}  |  Total cost: ${total_cost:.4f}",
        "",
        "## Ablation table (averaged across eval set)",
        "",
        "| Level | HCPC P | HCPC R | HCPC F1 | ICD-10 F1 | Halluc rate | Cost USD |",
        "|---|---|---|---|---|---|---|",
    ]
    for level in LEVELS:
        lines.append(
            f"| {level} "
            f"| {_agg(rows, level, 'hcpc_p')} "
            f"| {_agg(rows, level, 'hcpc_r')} "
            f"| {_agg(rows, level, 'hcpc_f1')} "
            f"| {_agg(rows, level, 'icd10_f1')} "
            f"| {_agg(rows, level, 'hallucination_rate')} "
            f"| {_agg(rows, level, 'cost_usd'):.5f} |"
        )

    lines += [
        "",
        "## Per-article breakdown",
        "",
        "| Article | Level | HCPC F1 | ICD-10 F1 | Halluc | Rules |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['article_id']} | {r['level']} "
            f"| {r['hcpc_f1']} | {r['icd10_f1']} "
            f"| {r['hallucination_rate']} | {r['n_rules']} |"
        )

    lines += [
        "",
        "## Limitations",
        "",
        "- Sample size is small (3 articles, ~30 rules total). Results are"
        " directionally useful but should not be taken as population-level estimates.",
        "- Ground truth is the official MCD code table, which may include codes"
        " not explicitly mentioned in the narrative (e.g. inherited from a parent LCD).",
        "- ICD-10 F1 is less meaningful for articles with zero covered codes.",
        "- Cost figures assume no cache hits; cached reruns cost $0.",
        "- No cross-model comparison or variance across multiple sampling runs.",
    ]

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args()
    run(model=args.model, n_articles=args.n)