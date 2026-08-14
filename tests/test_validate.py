"""Unit tests for the deterministic validation layer.

Runs without an API key or network — which is the point. The gate that
catches hallucinated citations should be provable on its own.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.validate import (  # noqa: E402
    find_span,
    is_valid_cpt_hcpcs,
    is_valid_icd10,
    is_valid_modifier,
    summarize,
    validate_extracted_rule,
)

SOURCE = (
    "Coverage Indications, Limitations, and/or Medical Necessity\n\n"
    "Nerve conduction studies (CPT 95907) are considered medically\n"
    "necessary   when performed for  the evaluation of carpal tunnel\n"
    "syndrome (ICD-10-CM G56.00). A maximum of two studies per date of\n"
    "service will be allowed. Modifier 59 is required when reported with\n"
    "code 95886."
)


def rule(**overrides):
    base = {
        "rule_type": "diagnosis_coverage",
        "trigger_codes": ["95907"],
        "conflicting_codes": [],
        "covered_icd10": ["G56.00"],
        "max_units": None,
        "required_modifiers": [],
        "place_of_service": [],
        "action": "DENY",
        "human_readable_reason": "Service not covered for the reported diagnosis.",
        "effective_date": "2026-01-01",
        "end_date": None,
        "quoted_span": "considered medically",
        "confidence": 0.92,
    }
    base.update(overrides)
    return base


# --- span matching --------------------------------------------------------

def test_exact_span():
    m = find_span("carpal tunnel", SOURCE)
    assert m is not None and m.mode == "exact"
    assert SOURCE[m.start:m.end] == "carpal tunnel"


def test_whitespace_normalized_span():
    # model re-wrapped the quote onto one line and collapsed spaces
    m = find_span("necessary when performed for the evaluation", SOURCE)
    assert m is not None and m.mode == "whitespace_normalized"
    assert "necessary" in SOURCE[m.start:m.end]
    assert "evaluation" in SOURCE[m.start:m.end]


def test_case_insensitive_span():
    m = find_span("NERVE CONDUCTION STUDIES", SOURCE)
    assert m is not None and m.mode == "case_insensitive"


def test_hallucinated_span_returns_none():
    m = find_span("prior authorization is required for all members", SOURCE)
    assert m is None


def test_empty_span_returns_none():
    assert find_span("", SOURCE) is None
    assert find_span("   ", SOURCE) is None


# --- code formats ---------------------------------------------------------

def test_cpt_hcpcs_formats():
    for good in ("95907", "0468U", "J0911", "G0008", "0075T"):
        assert is_valid_cpt_hcpcs(good), good
    for bad in ("9590", "959077", "ABCDE", "", "95-907"):
        assert not is_valid_cpt_hcpcs(bad), bad


def test_icd10_formats():
    for good in ("G56.00", "g5600", "E11.9", "Z00.00", "M25.511"):
        assert is_valid_icd10(good), good
    for bad in ("123.4", "U07.1x9x", "", "ICD10"):
        assert not is_valid_icd10(bad), bad


def test_modifier_formats():
    assert is_valid_modifier("59")
    assert is_valid_modifier("XU")
    assert not is_valid_modifier("593")
    assert not is_valid_modifier("5")


# --- rule validation ------------------------------------------------------

def test_clean_rule_passes():
    r = validate_extracted_rule(rule(), SOURCE)
    assert r.verdict == "PASS"
    assert not r.hallucinated
    assert r.issues == []


def test_hallucinated_citation_is_rejected():
    r = validate_extracted_rule(
        rule(quoted_span="prior authorization is required"), SOURCE
    )
    assert r.verdict == "REJECT"
    assert r.hallucinated


def test_low_confidence_routes_to_review():
    r = validate_extracted_rule(rule(confidence=0.41), SOURCE)
    assert r.verdict == "NEEDS_REVIEW"
    assert any("below threshold" in i for i in r.issues)


def test_missing_required_field_for_type_is_rejected():
    r = validate_extracted_rule(
        rule(rule_type="unit_limit", max_units=None), SOURCE
    )
    assert r.verdict == "REJECT"


def test_all_codes_malformed_is_rejected():
    r = validate_extracted_rule(
        rule(trigger_codes=["not-a-code"], covered_icd10=["nope"]), SOURCE
    )
    assert r.verdict == "REJECT"


def test_partially_malformed_codes_route_to_review():
    r = validate_extracted_rule(
        rule(trigger_codes=["95907", "bad-code"]), SOURCE
    )
    assert r.verdict == "NEEDS_REVIEW"


def test_self_conflicting_pair_is_flagged():
    r = validate_extracted_rule(
        rule(rule_type="code_pair_conflict",
             trigger_codes=["95907"],
             conflicting_codes=["95907"]),
        SOURCE,
    )
    assert any("both sides" in i for i in r.issues)


def test_fuzzy_span_downgrades_to_review():
    r = validate_extracted_rule(
        rule(quoted_span="NERVE CONDUCTION STUDIES"), SOURCE
    )
    assert r.verdict == "NEEDS_REVIEW"


def test_summarize_reports_rates():
    reports = [
        validate_extracted_rule(rule(), SOURCE),
        validate_extracted_rule(rule(quoted_span="does not exist here"), SOURCE),
        validate_extracted_rule(rule(confidence=0.2), SOURCE),
    ]
    s = summarize(reports)
    assert s["n"] == 3
    assert abs(s["hallucination_rate"] - 1 / 3) < 1e-9
    assert abs(s["pass_rate"] - 1 / 3) < 1e-9


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                passed += 1
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failed += 1
                print(f"  FAIL  {name}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
