"""Business impact of a quarterly edit update.

The diff tells you *what* changed. This tells you *what to do about it*:
which of the newly added edits are worth a reviewer's limited time.

For each edit added between two quarters we estimate an impact score
from how often the pair would plausibly appear together on a claim and
the dollars at stake if it does. The output is a ranked worklist, not a
single aggregate number, because a payer's real question is "given a
finite review team, what do we look at first?"

Charge amounts are estimates keyed off code type (see CHARGE_BANDS),
not real fee-schedule data. They are transparent placeholders so the
ranking is defensible; swap in real allowed amounts to productionise.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .diff import DiffResult, diff_quarters
from .engine import Claim, ClaimLine, PTPIndex
from .ncci import active_edits, find_quarter_fragments, load_ptp_quarter


# Rough charge bands by code family. Molecular/genomic PLA codes (nnnnU)
# and Category III (nnnnT) skew expensive; ordinary CPT is mid-range.
# These are order-of-magnitude estimates, stated openly.
CHARGE_BANDS = {
    "PLA": 900.0,      # 0000U-9999U proprietary lab analyses
    "CAT3": 600.0,     # 0000T-9999T emerging tech
    "HCPCS": 250.0,    # letter-prefixed (drugs, supplies, services)
    "CPT": 300.0,      # standard 5-digit CPT
}


def code_family(code: str) -> str:
    code = code.upper().strip()
    if len(code) == 5 and code[:4].isdigit() and code[4] == "U":
        return "PLA"
    if len(code) == 5 and code[:4].isdigit() and code[4] == "T":
        return "CAT3"
    if code[0].isalpha():
        return "HCPCS"
    return "CPT"


def estimated_charge(code: str) -> float:
    return CHARGE_BANDS[code_family(code)]


@dataclass
class ImpactRow:
    col1: str
    col2: str
    family: str
    denied_charge: float

    def as_dict(self) -> dict:
        return {
            "col1_code": self.col1,
            "col2_code": self.col2,
            "family": self.family,
            "denied_charge_usd": round(self.denied_charge, 2),
        }


def rank_added_edits(diff: DiffResult, new_index: PTPIndex) -> pd.DataFrame:
    """Rank the newly added edits by the dollars they would newly deny.

    For each added (col1, col2) pair we build the claim that would trigger
    it — both codes on one claim — and price the col2 line, since col2 is
    the code that gets denied. Pairs whose col2 carries a bypassable
    modifier are discounted, because a modifier can rescue the line.
    """
    rows: list[ImpactRow] = []
    for col1, col2 in sorted(diff.added_pairs):
        charge = estimated_charge(col2)

        # if this edit allows a modifier bypass, its unmitigated impact is
        # lower — weight it down rather than out
        rule = next((r for r in new_index.find_conflicts(col2) if r.col1_code == col1), None)
        if rule is not None and rule.modifier_allowed:
            charge *= 0.5

        rows.append(ImpactRow(col1, col2, code_family(col2), charge))

    df = pd.DataFrame(r.as_dict() for r in rows)
    if df.empty:
        return df
    return df.sort_values("denied_charge_usd", ascending=False).reset_index(drop=True)


def impact_by_family(ranked: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the added-edit impact by code family — the headline view.

    This is where the story lands: which category of medicine drives the
    quarter's new denials."""
    if ranked.empty:
        return ranked
    grouped = (
        ranked.groupby("family")
        .agg(edits_added=("col2_code", "count"),
             total_denied_usd=("denied_charge_usd", "sum"))
        .sort_values("total_denied_usd", ascending=False)
        .reset_index()
    )
    grouped["share_of_impact"] = (
        grouped["total_denied_usd"] / grouped["total_denied_usd"].sum()
    ).round(3)
    return grouped


def summarize(old_ver: str, new_ver: str,
              inbox: Path = Path("data/inbox")) -> dict:
    """End-to-end: load both quarters, diff, rank, and aggregate."""
    old_df = load_ptp_quarter(find_quarter_fragments(inbox, old_ver))
    new_df = active_edits(load_ptp_quarter(find_quarter_fragments(inbox, new_ver)))

    diff = diff_quarters(
        load_ptp_quarter(find_quarter_fragments(inbox, old_ver)),
        load_ptp_quarter(find_quarter_fragments(inbox, new_ver)),
        old_ver, new_ver,
    )
    new_index = PTPIndex.from_dataframe(new_df, new_ver)

    ranked = rank_added_edits(diff, new_index)
    by_family = impact_by_family(ranked)

    return {"diff": diff, "ranked": ranked, "by_family": by_family}


def main() -> None:
    import sys
    inbox = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/inbox")
    old_ver = sys.argv[2] if len(sys.argv) > 2 else "v321r0"
    new_ver = sys.argv[3] if len(sys.argv) > 3 else "v322r0"

    print(f"Analysing impact of {old_ver} -> {new_ver} ...\n")
    out = summarize(old_ver, new_ver, inbox)
    diff, ranked, by_family = out["diff"], out["ranked"], out["by_family"]

    print(f"Edits added this quarter: {diff.n_added:,}")
    print(f"Estimated new denied charges: "
          f"${ranked['denied_charge_usd'].sum():,.0f}\n")

    print("Impact by code family:")
    print(by_family.to_string(index=False))

    print(f"\nTop 15 highest-impact added edits (reviewer worklist):")
    print(ranked.head(15).to_string(index=False))


if __name__ == "__main__":
    main()