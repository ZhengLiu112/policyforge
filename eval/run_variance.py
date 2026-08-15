"""Variance analysis: is each level's F1 stable, or is it luck?

Runs every extraction level several times at non-zero temperature and
reports mean and standard deviation of HCPC F1. Caching is off so each
run is an independent sample rather than a cache hit.

Run:  python3 eval/run_variance.py --runs 5
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from src.extract import extract_rules
from src.llm import LLMClient
from src.mcd import load_articles, pick_eval_articles

# reuse the scoring helpers and level list from the main eval
from eval.run_eval import LEVELS, codes_from_rules, prf

POLICIES = Path("data/policies")
OUT_PATH = Path("eval/variance.md")


def f1_for_run(client: LLMClient, article, level) -> float:
    """Extract once and return HCPC F1 against the official code table."""
    outcome = extract_rules(
        client,
        level=level,
        doc_id=article.article_id,
        doc_title=article.title,
        doc_version=article.version,
        policy_text=article.narrative,
    )
    predicted = codes_from_rules(outcome.rules)
    return prf(predicted, article.hcpc_codes)["f1"]


def run(model: str, runs: int, temperature: float) -> None:
    articles = pick_eval_articles(load_articles(POLICIES), n=3)
    print(f"{len(articles)} articles, {runs} runs per level, temp={temperature}\n")

    # scores[level] = list of per-run mean-F1 across articles
    scores: dict[str, list[float]] = {lv: [] for lv in LEVELS}

    for level in LEVELS:
        for run_idx in range(runs):
            client = LLMClient(model=model, temperature=temperature, use_cache=False)
            per_article = [f1_for_run(client, a, level) for a in articles]
            mean_f1 = sum(per_article) / len(per_article)
            scores[level].append(mean_f1)
            print(f"  {level} run {run_idx + 1}/{runs}: F1={mean_f1:.3f}")
        print()

    write_report(scores, model, runs, temperature)
    print(f"Written to {OUT_PATH}")


def write_report(scores, model, runs, temperature) -> None:
    lines = [
        "# Variance Analysis",
        "",
        f"Model: `{model}`  |  Runs per level: {runs}  |  Temperature: {temperature}",
        "",
        "HCPC F1 across independent runs (caching disabled).",
        "",
        "| Level | Mean F1 | Std Dev | Min | Max |",
        "|---|---|---|---|---|",
    ]
    for level in LEVELS:
        vals = scores[level]
        mean = statistics.mean(vals)
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        lines.append(
            f"| {level} | {mean:.3f} | {sd:.3f} | {min(vals):.3f} | {max(vals):.3f} |"
        )

    lines += [
        "",
        "## Reading this table",
        "",
        "- A low standard deviation means the level's performance is stable "
        "and the headline F1 is trustworthy, not a lucky single run.",
        "- A high standard deviation means the level is sensitive to sampling "
        "and its point estimate should be treated with caution.",
        "- Structured levels (L1+) are expected to be more stable than the "
        "free-form baseline (L0), since a fixed schema removes one source of "
        "run-to-run variation.",
    ]
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--temperature", type=float, default=0.7)
    args = ap.parse_args()
    run(model=args.model, runs=args.runs, temperature=args.temperature)
