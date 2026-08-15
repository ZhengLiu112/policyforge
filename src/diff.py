"""
PolicyForge — NCCI PTP version diff.

Compares two quarters of PTP edits and produces:

  1. Structural diff: which (col1, col2) pairs were added, deleted, or
     had their modifier indicator changed.
  2. Summary statistics suitable for the report and slide deck.
  3. A DataFrame of changes that can be validated against the official
     CMS Quarterly Additions/Deletions/Revisions file — the "free
     correctness proof" described in the project plan.

No LLM involved. Every output is deterministic and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from .ncci import (
    CANONICAL_COLUMNS,
    active_edits,
    edit_pair_set,
    find_quarter_fragments,
    load_ptp_quarter,
)


@dataclass
class DiffResult:
    """The complete structural diff between two quarters."""

    old_version: str
    new_version: str

    old_total: int = 0
    new_total: int = 0
    old_active: int = 0
    new_active: int = 0

    added_pairs: set[tuple[str, str]] = field(default_factory=set)
    deleted_pairs: set[tuple[str, str]] = field(default_factory=set)
    modifier_changed: list[dict] = field(default_factory=list)

    @property
    def n_added(self) -> int:
        return len(self.added_pairs)

    @property
    def n_deleted(self) -> int:
        return len(self.deleted_pairs)

    @property
    def n_modifier_changed(self) -> int:
        return len(self.modifier_changed)

    @property
    def net_change(self) -> int:
        return self.n_added - self.n_deleted

    def summary_dict(self) -> dict:
        return {
            "old_version": self.old_version,
            "new_version": self.new_version,
            "old_total_pairs": self.old_total,
            "new_total_pairs": self.new_total,
            "old_active": self.old_active,
            "new_active": self.new_active,
            "added": self.n_added,
            "deleted": self.n_deleted,
            "modifier_changed": self.n_modifier_changed,
            "net_change": self.net_change,
        }

    def summary_text(self) -> str:
        lines = [
            f"NCCI PTP Diff: {self.old_version} → {self.new_version}",
            f"  Total pairs:      {self.old_total:>10,}  →  {self.new_total:>10,}  "
            f"(net {self.net_change:+,})",
            f"  Active pairs:     {self.old_active:>10,}  →  {self.new_active:>10,}  "
            f"(net {self.new_active - self.old_active:+,})",
            f"  Code pairs added:          {self.n_added:>10,}",
            f"  Code pairs deleted:        {self.n_deleted:>10,}",
            f"  Modifier indicator changed:{self.n_modifier_changed:>10,}",
        ]
        return "\n".join(lines)

    def added_df(self) -> pd.DataFrame:
        if not self.added_pairs:
            return pd.DataFrame(columns=["col1_code", "col2_code"])
        rows = sorted(self.added_pairs)
        return pd.DataFrame(rows, columns=["col1_code", "col2_code"])

    def deleted_df(self) -> pd.DataFrame:
        if not self.deleted_pairs:
            return pd.DataFrame(columns=["col1_code", "col2_code"])
        rows = sorted(self.deleted_pairs)
        return pd.DataFrame(rows, columns=["col1_code", "col2_code"])

    def modifier_changed_df(self) -> pd.DataFrame:
        if not self.modifier_changed:
            return pd.DataFrame(columns=[
                "col1_code", "col2_code", "old_modifier", "new_modifier"
            ])
        return pd.DataFrame(self.modifier_changed)


def diff_quarters(
    old_df: pd.DataFrame,
    new_df: pd.DataFrame,
    old_version: str,
    new_version: str,
) -> DiffResult:
    """Compare two quarter DataFrames (from load_ptp_quarter).

    Detects:
      * added:    pairs in new but not in old
      * deleted:  pairs in old but not in new
      * modifier_changed:  same pair present in both, but modifier_indicator differs
    """
    old_pairs = edit_pair_set(old_df)
    new_pairs = edit_pair_set(new_df)

    added = new_pairs - old_pairs
    deleted = old_pairs - new_pairs
    common = old_pairs & new_pairs

    # Build lookup for modifier comparison on common pairs
    def make_mod_map(df: pd.DataFrame) -> dict[tuple[str, str], str]:
        return {
            (str(r.col1_code), str(r.col2_code)): str(r.modifier_indicator).strip()
            for r in df.itertuples(index=False)
        }

    old_mod = make_mod_map(old_df)
    new_mod = make_mod_map(new_df)

    modifier_changed = []
    for pair in sorted(common):
        om = old_mod.get(pair, "")
        nm = new_mod.get(pair, "")
        if om != nm:
            modifier_changed.append({
                "col1_code": pair[0],
                "col2_code": pair[1],
                "old_modifier": om,
                "new_modifier": nm,
            })

    return DiffResult(
        old_version=old_version,
        new_version=new_version,
        old_total=len(old_df),
        new_total=len(new_df),
        old_active=len(active_edits(old_df)),
        new_active=len(active_edits(new_df)),
        added_pairs=added,
        deleted_pairs=deleted,
        modifier_changed=modifier_changed,
    )


# --------------------------------------------------------------------------
# Top 10 analysis — visual aid for the slide deck
# --------------------------------------------------------------------------

def top_changed_codes(result: DiffResult, n: int = 10) -> pd.DataFrame:
    """Which Column 1 codes were most affected by changes?

    Useful as a bar chart in the PPT: "these procedure groups had the
    most edit changes this quarter."
    """
    from collections import Counter
    counter: Counter[str] = Counter()
    for c1, _ in result.added_pairs:
        counter[c1] += 1
    for c1, _ in result.deleted_pairs:
        counter[c1] += 1
    for ch in result.modifier_changed:
        counter[ch["col1_code"]] += 1
    rows = counter.most_common(n)
    return pd.DataFrame(rows, columns=["col1_code", "total_changes"])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> None:
    import sys

    inbox = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/inbox")
    old_ver = sys.argv[2] if len(sys.argv) > 2 else "v321r0"
    new_ver = sys.argv[3] if len(sys.argv) > 3 else "v322r0"

    print(f"Loading {old_ver} ...")
    old_frags = find_quarter_fragments(inbox, old_ver)
    old_df = load_ptp_quarter(old_frags)

    print(f"Loading {new_ver} ...")
    new_frags = find_quarter_fragments(inbox, new_ver)
    new_df = load_ptp_quarter(new_frags)

    print("Computing diff ...")
    result = diff_quarters(old_df, new_df, old_ver, new_ver)

    print()
    print("=" * 60)
    print(result.summary_text())
    print("=" * 60)

    top = top_changed_codes(result)
    if not top.empty:
        print(f"\nTop {len(top)} most affected Column 1 codes:")
        print(top.to_string(index=False))

    # sample of additions
    added = result.added_df()
    if not added.empty:
        print(f"\nSample additions (first 10 of {result.n_added}):")
        print(added.head(10).to_string(index=False))

    # sample of deletions
    deleted = result.deleted_df()
    if not deleted.empty:
        print(f"\nSample deletions (first 10 of {result.n_deleted}):")
        print(deleted.head(10).to_string(index=False))

    if result.modifier_changed:
        print(f"\nModifier indicator changes ({result.n_modifier_changed}):")
        print(result.modifier_changed_df().head(10).to_string(index=False))


if __name__ == "__main__":
    main()
