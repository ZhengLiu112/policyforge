"""
PolicyForge — deterministic claims adjudication engine.

This module is the "engine disposes" half of the design commitment.
It takes a set of rules (PTP edits or RuleSpecs) and a set of claims,
and returns a decision for every claim line, with a citation to the
rule that triggered it.

No LLM anywhere in this module. Given the same rules and claims, every
run produces bit-identical output. This is the property that makes the
system auditable in a payer setting.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Literal, Optional

import pandas as pd


# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------

Action = Literal["PAY", "DENY", "PEND"]


@dataclass
class ClaimLine:
    line_id: str
    cpt_hcpcs: str
    units: int = 1
    modifiers: list[str] = field(default_factory=list)
    charge_amount: float = 0.0


@dataclass
class Claim:
    claim_id: str
    member_id: str
    date_of_service: date
    place_of_service: Optional[str] = None
    diagnosis_codes: list[str] = field(default_factory=list)
    lines: list[ClaimLine] = field(default_factory=list)

    @property
    def code_set(self) -> set[str]:
        return {ln.cpt_hcpcs.upper().strip() for ln in self.lines}


@dataclass
class Decision:
    claim_id: str
    line_id: str
    code: str
    action: Action
    rule_source: str          # e.g. "NCCI PTP v322r0"
    rule_pair: str            # e.g. "95907/95886"
    reason: str
    citation: str             # audit trail

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "line_id": self.line_id,
            "code": self.code,
            "action": self.action,
            "rule_source": self.rule_source,
            "rule_pair": self.rule_pair,
            "reason": self.reason,
            "citation": self.citation,
        }


# --------------------------------------------------------------------------
# PTP edit index (built from the NCCI loader's output)
# --------------------------------------------------------------------------

@dataclass
class PTPRule:
    col1_code: str
    col2_code: str
    modifier_allowed: bool
    rationale: str
    version: str              # e.g. "v322r0"

    @property
    def pair_label(self) -> str:
        return f"{self.col1_code}/{self.col2_code}"


class PTPIndex:
    """Fast lookup: given a set of codes on a claim, find conflicting pairs.

    Internally builds a mapping from each Column 2 code to all Column 1
    codes it conflicts with, because the denial falls on Column 2.
    """

    def __init__(self, rules: Iterable[PTPRule]) -> None:
        # col2 -> list of PTPRule
        self._by_col2: dict[str, list[PTPRule]] = {}
        self._n = 0
        for r in rules:
            self._by_col2.setdefault(r.col2_code, []).append(r)
            self._n += 1

    def __len__(self) -> int:
        return self._n

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame, version: str) -> "PTPIndex":
        rules = [
            PTPRule(
                col1_code=str(r.col1_code).strip().upper(),
                col2_code=str(r.col2_code).strip().upper(),
                modifier_allowed=(str(r.modifier_indicator).strip() == "1"),
                rationale=str(r.rationale).strip(),
                version=version,
            )
            for r in df.itertuples(index=False)
        ]
        return cls(rules)

    def find_conflicts(self, code: str) -> list[PTPRule]:
        """Return all PTP rules where this code is the Column 2 (denied) code."""
        return self._by_col2.get(code.upper().strip(), [])


# --------------------------------------------------------------------------
# Adjudication
# --------------------------------------------------------------------------

# NCCI PTP-associated modifiers that bypass an edit when modifier_allowed=1
PTP_BYPASS_MODIFIERS = {
    "25", "59", "91",
    "XE", "XS", "XP", "XU",   # -X{EPSU} modifiers
}


def adjudicate_claim(claim: Claim, index: PTPIndex) -> list[Decision]:
    """Apply PTP edits to a single claim. Returns one Decision per line.

    Logic:
      For each line L, check whether L's code appears as a Column 2 code
      in any PTP rule whose Column 1 code is also present on this claim.
      If so:
        - if modifier_allowed and L carries a bypass modifier → PAY
        - else → DENY the Column 2 line

      Lines with no conflict → PAY.
    """
    decisions: list[Decision] = []
    claim_codes = claim.code_set

    for line in claim.lines:
        code = line.cpt_hcpcs.upper().strip()
        conflicts = index.find_conflicts(code)

        denied = False
        for rule in conflicts:
            if rule.col1_code in claim_codes:
                # this line's code is Column 2, and Column 1 is also on the claim
                line_mods = {m.upper().strip() for m in line.modifiers}
                if rule.modifier_allowed and (line_mods & PTP_BYPASS_MODIFIERS):
                    # modifier bypass — allowed
                    decisions.append(Decision(
                        claim_id=claim.claim_id,
                        line_id=line.line_id,
                        code=code,
                        action="PAY",
                        rule_source=f"NCCI PTP {rule.version}",
                        rule_pair=rule.pair_label,
                        reason=f"PTP conflict with {rule.col1_code}, "
                               f"bypassed by modifier {line_mods & PTP_BYPASS_MODIFIERS}",
                        citation=f"NCCI PTP {rule.version}, "
                                 f"edit {rule.pair_label}, "
                                 f"modifier indicator=1, "
                                 f"rationale: {rule.rationale}",
                    ))
                else:
                    # denied
                    decisions.append(Decision(
                        claim_id=claim.claim_id,
                        line_id=line.line_id,
                        code=code,
                        action="DENY",
                        rule_source=f"NCCI PTP {rule.version}",
                        rule_pair=rule.pair_label,
                        reason=f"Column 2 code {code} denied when reported "
                               f"with Column 1 code {rule.col1_code}; "
                               f"modifier bypass {'not allowed' if not rule.modifier_allowed else 'allowed but no bypass modifier present'}",
                        citation=f"NCCI PTP {rule.version}, "
                                 f"edit {rule.pair_label}, "
                                 f"modifier indicator={'1' if rule.modifier_allowed else '0'}, "
                                 f"rationale: {rule.rationale}",
                    ))
                    denied = True
                    break  # one denial is enough

        if not denied:
            if not conflicts or code not in {r.col2_code for r in conflicts if r.col1_code in claim_codes}:
                decisions.append(Decision(
                    claim_id=claim.claim_id,
                    line_id=line.line_id,
                    code=code,
                    action="PAY",
                    rule_source="",
                    rule_pair="",
                    reason="No PTP conflict detected",
                    citation="No applicable edit",
                ))

    return decisions


def adjudicate_batch(
    claims: Iterable[Claim],
    index: PTPIndex,
) -> pd.DataFrame:
    """Adjudicate a batch of claims, return a tidy DataFrame."""
    rows = []
    for claim in claims:
        for d in adjudicate_claim(claim, index):
            rows.append(d.to_dict())
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Impact analysis (for diff)
# --------------------------------------------------------------------------

def impact_analysis(
    claims: list[Claim],
    old_index: PTPIndex,
    new_index: PTPIndex,
) -> pd.DataFrame:
    """Run the same claims through old and new rules, find flipped decisions.

    This is the "so what" of the diff: not just "which edits changed"
    but "which claims would be decided differently."
    """
    old_results = {
        (d.claim_id, d.line_id): d
        for claim in claims
        for d in adjudicate_claim(claim, old_index)
    }
    new_results = {
        (d.claim_id, d.line_id): d
        for claim in claims
        for d in adjudicate_claim(claim, new_index)
    }

    all_keys = set(old_results) | set(new_results)
    flips = []
    for key in sorted(all_keys):
        old_d = old_results.get(key)
        new_d = new_results.get(key)
        old_action = old_d.action if old_d else "N/A"
        new_action = new_d.action if new_d else "N/A"
        if old_action != new_action:
            flips.append({
                "claim_id": key[0],
                "line_id": key[1],
                "code": (new_d or old_d).code,
                "old_action": old_action,
                "new_action": new_action,
                "new_rule_pair": (new_d.rule_pair if new_d else ""),
                "new_reason": (new_d.reason if new_d else ""),
            })

    return pd.DataFrame(flips)


# --------------------------------------------------------------------------
# Synthetic claims generator (for demo and testing)
# --------------------------------------------------------------------------

def generate_test_claims(
    index: PTPIndex,
    n_conflict: int = 15,
    n_clean: int = 10,
    n_modifier_bypass: int = 5,
) -> list[Claim]:
    """Build claims that exercise the engine's main paths.

    Three categories:
      1. Conflict claims: two codes on the same claim that form a PTP pair → DENY
      2. Clean claims: codes with no PTP conflict → PAY
      3. Modifier bypass: conflict pair, but Column 2 line carries modifier 59 → PAY

    These are purpose-built test fixtures, not random data. Every claim's
    expected outcome is known in advance, so the demo can walk through them
    one by one and explain the logic.
    """
    claims: list[Claim] = []
    claim_counter = 0
    dos = date(2026, 7, 1)

    # collect some real PTP pairs from the index
    sample_rules: list[PTPRule] = []
    for rules_list in index._by_col2.values():
        for r in rules_list:
            sample_rules.append(r)
            if len(sample_rules) >= n_conflict + n_modifier_bypass + 10:
                break
        if len(sample_rules) >= n_conflict + n_modifier_bypass + 10:
            break

    # --- conflict claims (should DENY) ---
    for i, rule in enumerate(sample_rules[:n_conflict]):
        claim_counter += 1
        claims.append(Claim(
            claim_id=f"CLM-{claim_counter:04d}",
            member_id=f"MBR-{claim_counter:04d}",
            date_of_service=dos,
            lines=[
                ClaimLine(line_id="L1", cpt_hcpcs=rule.col1_code,
                          charge_amount=150.00),
                ClaimLine(line_id="L2", cpt_hcpcs=rule.col2_code,
                          charge_amount=100.00),
            ],
        ))

    # --- clean claims (should PAY all lines) ---
    # use Column 1 codes only, no conflicting Column 2
    used_codes = set()
    for rule in sample_rules:
        used_codes.add(rule.col1_code)
        used_codes.add(rule.col2_code)

    clean_codes = [r.col1_code for r in sample_rules
                   if r.col1_code not in {r2.col2_code for r2 in sample_rules}]
    for i in range(min(n_clean, len(clean_codes))):
        claim_counter += 1
        claims.append(Claim(
            claim_id=f"CLM-{claim_counter:04d}",
            member_id=f"MBR-{claim_counter:04d}",
            date_of_service=dos,
            lines=[
                ClaimLine(line_id="L1", cpt_hcpcs=clean_codes[i],
                          charge_amount=200.00),
            ],
        ))

    # --- modifier bypass claims (conflict pair, but modifier 59 → PAY) ---
    mod_bypass_rules = [r for r in sample_rules if r.modifier_allowed]
    for i, rule in enumerate(mod_bypass_rules[:n_modifier_bypass]):
        claim_counter += 1
        claims.append(Claim(
            claim_id=f"CLM-{claim_counter:04d}",
            member_id=f"MBR-{claim_counter:04d}",
            date_of_service=dos,
            lines=[
                ClaimLine(line_id="L1", cpt_hcpcs=rule.col1_code,
                          charge_amount=150.00),
                ClaimLine(line_id="L2", cpt_hcpcs=rule.col2_code,
                          modifiers=["59"],
                          charge_amount=100.00),
            ],
        ))

    return claims


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main() -> None:
    import sys
    from pathlib import Path
    from .ncci import active_edits, find_quarter_fragments, load_ptp_quarter

    inbox = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/inbox")
    version = sys.argv[2] if len(sys.argv) > 2 else "v322r0"

    print(f"Loading {version} ...")
    frags = find_quarter_fragments(inbox, version)
    df = load_ptp_quarter(frags)
    act = active_edits(df)
    index = PTPIndex.from_dataframe(act, version)
    print(f"  Built index: {len(index):,} active PTP rules\n")

    print("Generating test claims ...")
    claims = generate_test_claims(index)
    print(f"  {len(claims)} test claims generated\n")

    print("Adjudicating ...")
    results = adjudicate_batch(claims, index)
    print(f"  {len(results)} decisions\n")

    # summary
    action_counts = results["action"].value_counts()
    print("Decision summary:")
    for action, count in action_counts.items():
        print(f"  {action:6s}  {count}")

    # show a few examples
    print("\nSample decisions (first 10):")
    cols = ["claim_id", "line_id", "code", "action", "rule_pair", "reason"]
    print(results[cols].head(10).to_string(index=False))

    # charge impact
    code_to_charge = {}
    for c in claims:
        for ln in c.lines:
            code_to_charge[(c.claim_id, ln.line_id)] = ln.charge_amount

    results["charge_amount"] = results.apply(
        lambda r: code_to_charge.get((r["claim_id"], r["line_id"]), 0), axis=1
    )
    denied = results[results["action"] == "DENY"]
    print(f"\nTotal denied charge amount: ${denied['charge_amount'].sum():,.2f}")
    print(f"Total claims with at least one denial: "
          f"{denied['claim_id'].nunique()} / {len(claims)}")


if __name__ == "__main__":
    main()
