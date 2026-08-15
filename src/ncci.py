"""
PolicyForge — NCCI PTP edit file loader.

CMS ships PTP edits as tab-separated .txt files with a three-line
preamble that is not data:

    line 1:  CPT only copyright ... American Medical Association ...
    line 2:  Column1/Column2 Edits
    line 3:  Column 1 <tab> Column 2 <tab> *=in existence <tab> Effective ...
    line 4:  <tab><tab> prior to 1996 <tab> Date <tab> Date <tab> 0=not allowed
    line 5+: actual edit rows

The real column meaning, reconstructed from the split header:

    col 0  Column 1 code   (paid code)
    col 1  Column 2 code   (denied unless a modifier is present)
    col 2  "*"  -> edit existed prior to 1996  (else blank)
    col 3  Effective Date  (YYYYMMDD)
    col 4  Deletion Date   (YYYYMMDD, blank/"*" if still active)
    col 5  Modifier Indicator  0 = modifier not allowed to bypass
                               1 = modifier allowed
                               9 = not applicable
    col 6  PTP Edit Rationale (free text)

A full quarter is split across four files (f1-f4) purely for size; they
concatenate into one logical table. This module normalises one file and
loads a whole quarter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

CANONICAL_COLUMNS = [
    "col1_code",
    "col2_code",
    "pre_1996",
    "effective_date",
    "deletion_date",
    "modifier_indicator",
    "rationale",
]

# How many non-data lines sit at the top before the first edit row.
PREAMBLE_ROWS = 4


@dataclass
class PTPEdit:
    col1_code: str
    col2_code: str
    effective_date: Optional[str]
    deletion_date: Optional[str]
    modifier_indicator: str      # "0" | "1" | "9"
    rationale: str

    @property
    def is_active(self) -> bool:
        d = _clean_date(self.deletion_date)
        return d is None

    @property
    def modifier_allowed(self) -> bool:
        return str(self.modifier_indicator).strip() == "1"


def _clean_date(value: object) -> Optional[str]:
    # pandas may hand us a float NaN for empty cells; normalise first
    try:
        import math
        if isinstance(value, float) and math.isnan(value):
            return None
    except Exception:
        pass
    s = str(value).strip()
    if s in ("", "*", "nan", "None", "NaN"):
        return None
    # keep as YYYYMMDD string; downstream code parses if it needs a date
    return s


def load_ptp_file(path: Path) -> pd.DataFrame:
    """Load one NCCI PTP .txt file into a normalised DataFrame.

    Skips the 4-line preamble, assigns canonical column names, strips the
    embedded copyright footer row if present, and drops blank rows.
    """
    df = pd.read_csv(
        path,
        sep="\t",
        dtype=str,
        skiprows=PREAMBLE_ROWS,
        header=None,
        names=CANONICAL_COLUMNS,
        keep_default_na=False,
        encoding="utf-8-sig",
        engine="python",
        on_bad_lines="skip",
    )

    # a code must be present in both code columns for the row to be real
    df["col1_code"] = df["col1_code"].str.strip().str.upper()
    df["col2_code"] = df["col2_code"].str.strip().str.upper()
    df = df[(df["col1_code"] != "") & (df["col2_code"] != "")]

    # drop any trailing copyright / note lines that survived
    df = df[~df["col1_code"].str.contains("COPYRIGHT", case=False, na=False)]
    df = df[df["col1_code"].str.match(r"^(?:\d{5}|\d{4}[A-Z]|[A-Z]\d{4})$", na=False)]

    df["effective_date"] = df["effective_date"].map(_clean_date)
    df["deletion_date"] = df["deletion_date"].map(_clean_date)
    df["modifier_indicator"] = df["modifier_indicator"].str.strip()
    df["rationale"] = df["rationale"].str.strip()

    return df.reset_index(drop=True)


def load_ptp_quarter(files: Iterable[Path]) -> pd.DataFrame:
    """Concatenate the four fragment files of one quarter into one table.

    Accepts any iterable of paths; caller decides which quarter's fragments
    to pass. Deduplicates on the (col1, col2) pair, keeping the first.
    """
    frames = [load_ptp_file(p) for p in files]
    if not frames:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["col1_code", "col2_code"], keep="first")
    return combined.reset_index(drop=True)


def active_edits(df: pd.DataFrame) -> pd.DataFrame:
    """Rows with no deletion date — the edits currently in force."""
    col = df["deletion_date"]
    mask = col.isna() | (col.astype(str).str.strip().isin(["", "*", "nan", "NaN", "None"]))
    return df[mask].reset_index(drop=True)


def edit_pair_set(df: pd.DataFrame) -> set[tuple[str, str]]:
    """The set of (col1, col2) pairs — used for version diffing."""
    return set(zip(df["col1_code"], df["col2_code"]))


def to_edits(df: pd.DataFrame) -> list[PTPEdit]:
    return [
        PTPEdit(
            col1_code=str(r.col1_code),
            col2_code=str(r.col2_code),
            effective_date=_clean_date(r.effective_date),
            deletion_date=_clean_date(r.deletion_date),
            modifier_indicator=str(r.modifier_indicator).strip(),
            rationale=str(r.rationale),
        )
        for r in df.itertuples(index=False)
    ]


# --------------------------------------------------------------------------
# fragment discovery
# --------------------------------------------------------------------------

def find_quarter_fragments(search_root: Path, version: str) -> list[Path]:
    """Find the .txt fragments for a given version string (e.g. 'v322r0').

    CMS names them ccipra-<version>-f1..f4.txt, sometimes with a trailing
    _0 and mixed .TXT/.txt casing. This globs case-insensitively and
    returns them sorted by fragment number.
    """
    matches: list[Path] = []
    for p in search_root.rglob("*"):
        if not p.is_file():
            continue
        name = p.name.lower()
        if version.lower() in name and name.endswith((".txt",)):
            matches.append(p)

    def frag_num(p: Path) -> int:
        import re
        m = re.search(r"-f(\d+)", p.name.lower())
        return int(m.group(1)) if m else 0

    return sorted(matches, key=frag_num)


if __name__ == "__main__":
    import sys
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/inbox")
    for version in ("v321r0", "v322r0"):
        frags = find_quarter_fragments(root, version)
        if not frags:
            print(f"{version}: no fragments found under {root}")
            continue
        df = load_ptp_quarter(frags)
        act = active_edits(df)
        print(f"{version}: {len(frags)} fragments, "
              f"{len(df):,} unique pairs, {len(act):,} active")
        print(df.head(3).to_string(index=False))
        print()
