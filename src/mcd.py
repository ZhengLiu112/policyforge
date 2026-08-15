"""Medicare Coverage Database loader.

Loads Billing & Coding Articles and their official code tables. The
article narrative (an HTML blob in `description`) is what we feed the
model; the code tables are the answer key we score against.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

POLICIES = Path("data/policies")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


def strip_html(raw: str) -> str:
    """Turn the stored HTML into readable plain text.

    Block tags become newlines, list items get a bullet, entities are
    unescaped. Good enough to preserve the structure a reader relies on
    without carrying markup into the prompt.
    """
    if not isinstance(raw, str) or not raw:
        return ""
    text = raw
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = re.sub(r"(?i)</(ul|ol|div|tr|table)>", "\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()


@dataclass
class Article:
    article_id: str
    version: str
    title: str
    narrative: str                     # cleaned plain text
    hcpc_codes: set[str] = field(default_factory=set)
    icd10_covered: set[str] = field(default_factory=set)
    icd10_noncovered: set[str] = field(default_factory=set)

    @property
    def is_billing_coding(self) -> bool:
        return self.title.lower().startswith("billing and coding")

    def code_counts(self) -> dict:
        return {
            "hcpc": len(self.hcpc_codes),
            "icd10_covered": len(self.icd10_covered),
            "icd10_noncovered": len(self.icd10_noncovered),
        }


def _read(name: str, sep: str = ",") -> pd.DataFrame:
    df = pd.read_csv(POLICIES / name, sep=sep, dtype=str,
                     keep_default_na=False, encoding="utf-8-sig",
                     engine="python", on_bad_lines="skip")
    return df.fillna("")


def load_articles(policies_dir: Path = POLICIES) -> dict[str, Article]:
    """Load all articles keyed by article_id, with code tables attached."""
    global POLICIES
    POLICIES = policies_dir

    meta = _read("article.csv")
    hcpc = _read("article_x_hcpc_code.csv")
    cov = _read("article_x_icd10_covered.csv")
    noncov = _read("article_x_icd10_noncovered.csv")

    articles: dict[str, Article] = {}
    for r in meta.itertuples(index=False):
        articles[r.article_id] = Article(
            article_id=str(r.article_id),
            version=str(r.article_version),
            title=str(r.title) if isinstance(r.title, str) else "",
            narrative=strip_html(r.description),
        )

    for r in hcpc.itertuples(index=False):
        a = articles.get(r.article_id)
        if a:
            a.hcpc_codes.add(str(r.hcpc_code_id).strip().upper())

    for r in cov.itertuples(index=False):
        a = articles.get(r.article_id)
        if a:
            a.icd10_covered.add(str(r.icd10_code_id).strip().upper())

    for r in noncov.itertuples(index=False):
        a = articles.get(r.article_id)
        if a:
            a.icd10_noncovered.add(str(r.icd10_code_id).strip().upper())

    return articles


def pick_eval_articles(
    articles: dict[str, Article],
    n: int = 3,
    min_hcpc: int = 2,
    max_hcpc: int = 25,
    min_narrative: int = 400,
    max_narrative: int = 6000,
) -> list[Article]:
    """Select a few articles suited to evaluation: billing-and-coding type,
    a modest code table (so scoring is meaningful but not dominated by one
    giant list), and a narrative long enough to carry real content."""
    candidates = [
        a for a in articles.values()
        if a.is_billing_coding
        and min_hcpc <= len(a.hcpc_codes) <= max_hcpc
        and min_narrative <= len(a.narrative) <= max_narrative
    ]
    candidates.sort(key=lambda a: (len(a.hcpc_codes), len(a.narrative)))
    step = max(1, len(candidates) // n)
    return candidates[::step][:n]


def load_lcd_meta(policies_dir: Path = POLICIES) -> pd.DataFrame:
    global POLICIES
    POLICIES = policies_dir
    return _read("lcd.csv")


def main() -> None:
    import sys
    policies = Path(sys.argv[1]) if len(sys.argv) > 1 else POLICIES

    articles = load_articles(policies)
    bc = [a for a in articles.values() if a.is_billing_coding]
    print(f"{len(articles):,} articles, {len(bc):,} billing-and-coding")

    picks = pick_eval_articles(articles)
    print(f"\nSelected {len(picks)} evaluation articles:\n")
    for a in picks:
        print(f"  [{a.article_id}] {a.title[:70]}")
        print(f"      narrative {len(a.narrative)} chars | codes {a.code_counts()}")
        print(f"      hcpc: {sorted(a.hcpc_codes)}")
        print()


if __name__ == "__main__":
    main()