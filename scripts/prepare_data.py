#!/usr/bin/env python3
"""
PolicyForge — automated data preparation.

You do the one thing a script cannot: click the download buttons on the
CMS website (NCCI and MCD require license-acceptance clicks and rotate
their URLs every quarter, so a wget one-liner tends to return an HTML
page instead of data).

Everything after that is automated. Drop whatever you downloaded into
   data/inbox/
and run
   python3 scripts/prepare_data.py

The script will:
  * unzip every archive it finds (recursively)
  * sniff each tabular file's real delimiter, encoding, and columns
  * classify files as NCCI PTP / MUE / MCD article / MCD LCD / data dict
  * copy the useful ones into data/ncci and data/policies
  * print a structure report and write data/STRUCTURE_REPORT.md
  * tell you exactly what, if anything, is still missing

It reads nothing you have to configure. It guesses from content, and it
tells you what it guessed so you can correct it.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "data" / "inbox"
NCCI_DIR = ROOT / "data" / "ncci"
POLICIES_DIR = ROOT / "data" / "policies"
REPORT_PATH = ROOT / "data" / "STRUCTURE_REPORT.md"

TABULAR_EXTS = {".txt", ".csv", ".tsv"}
DOC_EXTS = {".pdf"}
DB_EXTS = {".mdb", ".accdb"}


# --------------------------------------------------------------------------
# unzip
# --------------------------------------------------------------------------

def unzip_all(inbox: Path) -> None:
    """Recursively unzip every archive under inbox, in place."""
    changed = True
    while changed:
        changed = False
        for zf in list(inbox.rglob("*.zip")):
            target = zf.with_suffix("")
            if target.exists():
                continue
            try:
                with zipfile.ZipFile(zf) as z:
                    z.extractall(target)
                print(f"  unzipped  {zf.relative_to(inbox)}")
                changed = True
            except zipfile.BadZipFile:
                print(f"  WARNING   {zf.name} is not a valid zip "
                      f"(likely an HTML error page — re-download it)")


# --------------------------------------------------------------------------
# sniff a tabular file
# --------------------------------------------------------------------------

def read_head_bytes(path: Path, n: int = 16384) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def detect_encoding(head: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            head.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"  # always succeeds


def looks_like_html(head: bytes) -> bool:
    start = head[:512].lstrip().lower()
    return start.startswith(b"<!doctype html") or start.startswith(b"<html")


def detect_delimiter(sample: str) -> str:
    candidates = ["|", "\t", ",", ";"]
    # score by consistency of column count across the first lines
    lines = [ln for ln in sample.splitlines() if ln.strip()][:20]
    best, best_score = ",", -1.0
    for delim in candidates:
        counts = [ln.count(delim) for ln in lines]
        if not counts or max(counts) == 0:
            continue
        # reward high, consistent counts
        avg = sum(counts) / len(counts)
        spread = max(counts) - min(counts)
        score = avg - spread
        if score > best_score:
            best, best_score = delim, score
    return best


def has_header(sample: str, delim: str) -> bool:
    lines = [ln for ln in sample.splitlines() if ln.strip()][:2]
    if len(lines) < 2:
        return True
    first = lines[0].split(delim)
    # header heuristic: first row has few pure-numeric cells
    numeric = sum(1 for c in first if c.strip().replace(".", "").isdigit())
    return numeric <= len(first) // 3


def sniff_tabular(path: Path) -> dict:
    head = read_head_bytes(path)
    if looks_like_html(head):
        return {"path": str(path), "kind": "HTML_ERROR",
                "note": "downloaded file is an HTML page, not data — re-download"}
    enc = detect_encoding(head)
    sample = head.decode(enc, errors="replace")
    delim = detect_delimiter(sample)
    header = has_header(sample, delim)

    reader = csv.reader(io.StringIO(sample), delimiter=delim)
    rows = list(reader)[:6]
    columns = rows[0] if (header and rows) else \
        [f"col{i}" for i in range(len(rows[0]))] if rows else []

    # count data lines cheaply
    with open(path, "rb") as f:
        line_count = sum(1 for _ in f)

    return {
        "path": str(path.relative_to(ROOT)),
        "kind": "tabular",
        "encoding": enc,
        "delimiter": {"|": "pipe", "\t": "tab", ",": "comma", ";": "semicolon"}.get(delim, delim),
        "delimiter_char": delim,
        "has_header": header,
        "n_columns": len(columns),
        "columns": [c.strip() for c in columns],
        "approx_rows": max(0, line_count - (1 if header else 0)),
        "preview": rows[1:4] if header else rows[:3],
    }


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------

def classify(info: dict, path: Path) -> str:
    name = path.name.lower()
    cols = " ".join(info.get("columns", [])).lower()
    preview_text = " ".join(
        " ".join(str(c) for c in row) for row in info.get("preview", [])
    ).lower()
    blob = f"{cols} {preview_text}"

    # CMS abbreviations: ccipra = practitioner PTP, ccihra = hospital PTP.
    # Their real header is on line 3, so the sniffer's "columns" is the
    # copyright line; classify from filename + the preview rows instead.
    if "additions" in name or "deletions" in name or "revisions" in name:
        return "ncci_change_log"
    if name.startswith("ccipra") or name.startswith("ccihra") or "ptp" in name:
        return "ncci_ptp"
    if "column1/column2" in blob or ("column 1" in blob and "column 2" in blob):
        return "ncci_ptp"
    if "mue" in name or "medically unlikely" in blob or "mue value" in blob:
        return "ncci_mue"
    if "hcpc" in cols and ("article" in name or "lcd" in name):
        return "mcd_code_table"
    if "lcd" in name:
        return "mcd_lcd"
    if "article" in name or name.startswith("art"):
        return "mcd_article"
    return "unknown"


CLASS_DEST = {
    "ncci_ptp": NCCI_DIR,
    "ncci_mue": NCCI_DIR,
    "ncci_change_log": NCCI_DIR,
    "mcd_article": POLICIES_DIR,
    "mcd_lcd": POLICIES_DIR,
    "mcd_code_table": POLICIES_DIR,
}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    INBOX.mkdir(parents=True, exist_ok=True)
    NCCI_DIR.mkdir(parents=True, exist_ok=True)
    POLICIES_DIR.mkdir(parents=True, exist_ok=True)

    contents = [p for p in INBOX.rglob("*") if p.is_file()]
    if not contents:
        print(f"\n  data/inbox/ is empty.\n\n"
              f"  Download the CMS files (see DATA_ACQUISITION.md), put them\n"
              f"  in  {INBOX}\n"
              f"  and run this script again. Zips are fine — they get unpacked.\n")
        return 1

    print("\n[1/4] Unzipping archives ...")
    unzip_all(INBOX)

    print("\n[2/4] Sniffing files ...")
    tabular_reports: list[dict] = []
    docs: list[Path] = []
    dbs: list[Path] = []
    html_errors: list[str] = []

    for path in sorted(INBOX.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in TABULAR_EXTS:
            info = sniff_tabular(path)
            if info.get("kind") == "HTML_ERROR":
                html_errors.append(info["path"])
                continue
            info["class"] = classify(info, path)
            tabular_reports.append(info)
        elif ext in DOC_EXTS:
            docs.append(path)
        elif ext in DB_EXTS:
            dbs.append(path)

    print("\n[3/4] Copying recognised files into place ...")
    copied: list[tuple[str, str]] = []
    for info in tabular_reports:
        cls = info["class"]
        dest_dir = CLASS_DEST.get(cls)
        if dest_dir is None:
            continue
        src = ROOT / info["path"]
        dest = dest_dir / src.name
        if not dest.exists():
            shutil.copy2(src, dest)
        copied.append((cls, str(dest.relative_to(ROOT))))

    print("\n[4/4] Writing report ...")
    write_report(tabular_reports, docs, dbs, html_errors, copied)

    # --- console summary --------------------------------------------------
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    by_class: dict[str, int] = {}
    for info in tabular_reports:
        by_class[info["class"]] = by_class.get(info["class"], 0) + 1
    for cls, n in sorted(by_class.items()):
        print(f"  {cls:20s} {n} file(s)")
    print(f"  {'pdf docs':20s} {len(docs)} file(s)")
    print(f"  {'access db':20s} {len(dbs)} file(s)")

    if html_errors:
        print("\n  ⚠ These downloads are HTML pages, not data — re-download:")
        for h in html_errors:
            print(f"      {h}")

    print("\n  Detected NCCI/MCD data dictionaries (read these to decide")
    print("  main-plan vs plan-B for Eval 1):")
    for d in docs:
        if "dict" in d.name.lower() or "readme" in d.name.lower() or "read me" in d.name.lower():
            print(f"      {d.relative_to(ROOT)}")

    missing = check_missing(by_class)
    if missing:
        print("\n  Still missing:")
        for m in missing:
            print(f"      - {m}")
    else:
        print("\n  ✓ Core data present. Paste data/STRUCTURE_REPORT.md back to continue.")

    print(f"\n  Full report: {REPORT_PATH.relative_to(ROOT)}\n")
    return 0


def check_missing(by_class: dict[str, int]) -> list[str]:
    missing = []
    if not by_class.get("ncci_ptp"):
        missing.append("NCCI PTP edits (current quarter)")
    if not by_class.get("mcd_article") and not by_class.get("mcd_code_table"):
        missing.append("MCD Article data (for Eval 1 main plan)")
    if not by_class.get("ncci_mue"):
        missing.append("NCCI MUE (needed only if you fall back to Plan B)")
    return missing


def write_report(tabular, docs, dbs, html_errors, copied) -> None:
    lines = ["# Data Structure Report", "",
             "Auto-generated by scripts/prepare_data.py. Paste this back to",
             "continue to Phase 1 — it tells the code the real column names.", ""]

    lines.append("## Tabular files\n")
    for info in tabular:
        lines.append(f"### `{Path(info['path']).name}`  →  **{info['class']}**")
        lines.append(f"- delimiter: {info['delimiter']}  |  encoding: {info['encoding']}"
                     f"  |  header: {info['has_header']}  |  ~rows: {info['approx_rows']}")
        lines.append(f"- columns ({info['n_columns']}): `{info['columns']}`")
        if info.get("preview"):
            lines.append(f"- sample rows:")
            for row in info["preview"]:
                lines.append(f"    - `{row}`")
        lines.append("")

    if docs:
        lines.append("## PDF documents\n")
        for d in docs:
            lines.append(f"- `{d.relative_to(ROOT)}`")
        lines.append("")
    if dbs:
        lines.append("## Access databases (.mdb)\n")
        for d in dbs:
            lines.append(f"- `{d.relative_to(ROOT)}`")
        lines.append("")
    if html_errors:
        lines.append("## ⚠ Bad downloads (HTML, not data)\n")
        for h in html_errors:
            lines.append(f"- `{h}` — re-download")
        lines.append("")
    if copied:
        lines.append("## Copied into place\n")
        for cls, dest in copied:
            lines.append(f"- {cls} → `{dest}`")
        lines.append("")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
