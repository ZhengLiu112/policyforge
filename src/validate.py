"""
PolicyForge — deterministic validation layer.

This module is the reason the ablation study has a fourth bar. It runs
after every extraction, costs nothing, uses no model, and catches the
failure mode that matters most in a payer setting: a rule that cites
text which does not exist in the source document.

Pure standard library on purpose — it must be unit-testable without an
API key, and it must not depend on the schema layer so it can be reused
against raw dicts during evaluation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional

Verdict = Literal["PASS", "NEEDS_REVIEW", "REJECT"]

DEFAULT_CONFIDENCE_THRESHOLD = 0.70

# --------------------------------------------------------------------------
# Code format validation
# --------------------------------------------------------------------------

# CPT Category I:      5 digits                      99213
# CPT Category II/III: 4 digits + F / T              0509F, 0075T
# PLA codes:           4 digits + U                  0468U
# HCPCS Level II:      letter + 4 digits             J0911, G0008
_CPT_HCPCS_RE = re.compile(r"^(?:\d{5}|\d{4}[A-Z]|[A-Z]\d{4})$")

# ICD-10-CM: letter (not U, not I/O confusion), digit, alnum, then optional
# subclassification of up to 4 alphanumerics after a dot.
_ICD10_RE = re.compile(r"^[A-TV-Z]\d[0-9A-Z](?:\.?[0-9A-Z]{1,4})?$")

# Claim modifiers are two alphanumeric characters (25, 59, LT, XU, ...)
_MODIFIER_RE = re.compile(r"^[0-9A-Z]{2}$")

# CMS place-of-service codes are two digits (11 office, 21 inpatient, ...)
_POS_RE = re.compile(r"^\d{2}$")


def is_valid_cpt_hcpcs(code: str) -> bool:
    return bool(_CPT_HCPCS_RE.match(str(code).strip().upper()))


def is_valid_icd10(code: str) -> bool:
    return bool(_ICD10_RE.match(str(code).strip().upper().replace(" ", "")))


def is_valid_modifier(code: str) -> bool:
    return bool(_MODIFIER_RE.match(str(code).strip().upper()))


def is_valid_pos(code: str) -> bool:
    return bool(_POS_RE.match(str(code).strip()))


# --------------------------------------------------------------------------
# Span verification — the hallucination gate
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class SpanMatch:
    start: int
    end: int
    mode: Literal["exact", "whitespace_normalized", "case_insensitive"]


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Collapse runs of whitespace to a single space.

    Returns the normalized string plus an index map so a position in the
    normalized string can be translated back to a position in the original.
    Needed because models routinely re-wrap quoted text: the quote is
    faithful, the whitespace is not, and a naive `in` check would score
    that as a hallucination.
    """
    chars: list[str] = []
    index_map: list[int] = []
    prev_was_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_was_space:
                continue
            chars.append(" ")
            index_map.append(i)
            prev_was_space = True
        else:
            chars.append(ch)
            index_map.append(i)
            prev_was_space = False
    return "".join(chars), index_map


def find_span(quoted: str, source: str) -> Optional[SpanMatch]:
    """Locate `quoted` inside `source`. Returns None if it is not there.

    None is the signal that the model invented the citation. That event is
    counted directly as the hallucination rate — no human labelling needed,
    which is what makes it a usable continuous metric.
    """
    if not quoted or not quoted.strip():
        return None

    # 1. exact
    pos = source.find(quoted)
    if pos != -1:
        return SpanMatch(pos, pos + len(quoted), "exact")

    # 2. whitespace-normalized
    norm_src, idx_map = _normalize_with_map(source)
    norm_q, _ = _normalize_with_map(quoted)
    norm_q = norm_q.strip()
    if norm_q:
        pos = norm_src.find(norm_q)
        if pos != -1:
            return SpanMatch(idx_map[pos], idx_map[pos + len(norm_q) - 1] + 1,
                             "whitespace_normalized")

        # 3. case-insensitive, still whitespace-normalized
        pos = norm_src.lower().find(norm_q.lower())
        if pos != -1:
            return SpanMatch(idx_map[pos], idx_map[pos + len(norm_q) - 1] + 1,
                             "case_insensitive")

    return None


# --------------------------------------------------------------------------
# Rule-level validation
# --------------------------------------------------------------------------

@dataclass
class ValidationReport:
    verdict: Verdict
    issues: list[str] = field(default_factory=list)
    span: Optional[SpanMatch] = None

    @property
    def hallucinated(self) -> bool:
        return self.span is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "issues": list(self.issues),
            "span": None if self.span is None
                    else {"start": self.span.start, "end": self.span.end,
                          "mode": self.span.mode},
        }


# Which fields each rule type is required to populate to be meaningful.
_REQUIRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "code_pair_conflict":    ("trigger_codes", "conflicting_codes"),
    "unit_limit":            ("trigger_codes", "max_units"),
    "diagnosis_coverage":    ("trigger_codes", "covered_icd10"),
    "modifier_required":     ("trigger_codes", "required_modifiers"),
    "frequency_limit":       ("trigger_codes",),
    "place_of_service":      ("trigger_codes", "place_of_service"),
    "documentation_required": ("trigger_codes",),
}


def _get(rule: Any, name: str) -> Any:
    return rule.get(name) if isinstance(rule, dict) else getattr(rule, name, None)


def _check_codes(values: Iterable[str], predicate, label: str,
                 issues: list[str]) -> int:
    """Returns the number of invalid entries."""
    bad = [v for v in (values or []) if not predicate(v)]
    if bad:
        issues.append(f"{label}: malformed {bad[:5]}"
                      + (f" (+{len(bad) - 5} more)" if len(bad) > 5 else ""))
    return len(bad)


def validate_extracted_rule(
    rule: Any,
    source_text: str,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> ValidationReport:
    """Validate one extracted rule against the document it came from.

    Accepts an ExtractedRule or a plain dict, so the same function serves
    the runtime pipeline and the offline evaluation harness.

    Verdict semantics:
      REJECT        the rule is unusable — quoted span not in source, or
                    the rule type's required fields are empty, or every
                    code is malformed. Never reaches a human.
      NEEDS_REVIEW  usable but uncertain — low confidence, partially
                    malformed codes, or a fuzzy span match.
      PASS          eligible for the approval queue.

    PASS still does not mean executable. Only human approval does.
    """
    issues: list[str] = []

    # --- 1. the hallucination gate ---------------------------------------
    quoted = _get(rule, "quoted_span") or ""
    span = find_span(quoted, source_text)
    if span is None:
        issues.append("quoted_span is not present in the source document")
        return ValidationReport("REJECT", issues, None)
    if span.mode != "exact":
        issues.append(f"quoted_span matched only after {span.mode} normalisation")

    # --- 2. confidence ----------------------------------------------------
    try:
        confidence = float(_get(rule, "confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
        issues.append("confidence missing or non-numeric; treated as 0.0")
    if not 0.0 <= confidence <= 1.0:
        issues.append(f"confidence {confidence} outside [0,1]; clamped")
        confidence = max(0.0, min(1.0, confidence))

    # --- 3. code formats --------------------------------------------------
    trigger = _get(rule, "trigger_codes") or []
    conflicting = _get(rule, "conflicting_codes") or []
    icd10 = _get(rule, "covered_icd10") or []
    modifiers = _get(rule, "required_modifiers") or []
    pos_codes = _get(rule, "place_of_service") or []

    bad = 0
    bad += _check_codes(trigger, is_valid_cpt_hcpcs, "trigger_codes", issues)
    bad += _check_codes(conflicting, is_valid_cpt_hcpcs, "conflicting_codes", issues)
    bad += _check_codes(icd10, is_valid_icd10, "covered_icd10", issues)
    bad += _check_codes(modifiers, is_valid_modifier, "required_modifiers", issues)
    bad += _check_codes(pos_codes, is_valid_pos, "place_of_service", issues)

    total_codes = len(trigger) + len(conflicting) + len(icd10) + len(modifiers) + len(pos_codes)
    if total_codes and bad == total_codes:
        issues.append("every extracted code is malformed")
        return ValidationReport("REJECT", issues, span)

    # --- 4. rule-type completeness ----------------------------------------
    rule_type = _get(rule, "rule_type")
    for required_field in _REQUIRED_BY_TYPE.get(rule_type, ()):
        value = _get(rule, required_field)
        if value is None or (isinstance(value, (list, tuple, str)) and len(value) == 0):
            issues.append(f"rule_type '{rule_type}' requires non-empty '{required_field}'")
            return ValidationReport("REJECT", issues, span)

    # --- 5. internal consistency ------------------------------------------
    if rule_type == "code_pair_conflict":
        overlap = set(map(str.upper, map(str, trigger))) & set(map(str.upper, map(str, conflicting)))
        if overlap:
            issues.append(f"code appears on both sides of the conflict: {sorted(overlap)}")

    if rule_type == "unit_limit":
        max_units = _get(rule, "max_units")
        if isinstance(max_units, int) and max_units < 0:
            issues.append("max_units is negative")
            return ValidationReport("REJECT", issues, span)

    reason = _get(rule, "human_readable_reason") or ""
    if _get(rule, "action") in ("DENY", "PEND") and len(reason.strip()) < 10:
        issues.append("denial/pend action without a usable reason string")

    # --- 6. verdict -------------------------------------------------------
    if confidence < confidence_threshold:
        issues.append(f"confidence {confidence:.2f} below threshold {confidence_threshold:.2f}")
        return ValidationReport("NEEDS_REVIEW", issues, span)
    if bad > 0 or span.mode != "exact":
        return ValidationReport("NEEDS_REVIEW", issues, span)
    return ValidationReport("PASS", issues, span)


def summarize(reports: list[ValidationReport]) -> dict[str, float]:
    """Aggregate metrics for the ablation table. All computed automatically —
    no human annotation anywhere in this function."""
    n = len(reports) or 1
    return {
        "n": len(reports),
        "hallucination_rate": sum(r.hallucinated for r in reports) / n,
        "reject_rate": sum(r.verdict == "REJECT" for r in reports) / n,
        "needs_review_rate": sum(r.verdict == "NEEDS_REVIEW" for r in reports) / n,
        "pass_rate": sum(r.verdict == "PASS" for r in reports) / n,
        "exact_span_rate": sum(
            r.span is not None and r.span.mode == "exact" for r in reports
        ) / n,
    }
