"""
PolicyForge — data contracts.

Two-layer schema design:

  ExtractionResult / ExtractedRule   -> LLM-facing. Must satisfy OpenAI
                                        Structured Outputs *strict* mode:
                                        no defaults, no numeric/string
                                        constraints, no `format`, every
                                        property required, objects closed.

  RuleSpec / Provenance              -> internal domain model. Rich
                                        validation, real date objects,
                                        governance metadata, defaults.

Keeping these separate is not ceremony: strict mode silently rejects a
schema carrying `default` or `minimum`, and a domain model without those
constraints is not safe to execute against claims. `promote()` is the
one place where an untrusted LLM object becomes a trusted domain object.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# --------------------------------------------------------------------------
# Controlled vocabularies
# --------------------------------------------------------------------------

RuleType = Literal[
    "code_pair_conflict",     # NCCI PTP: two codes may not be reported together
    "unit_limit",             # NCCI MUE: max units of service
    "diagnosis_coverage",     # LCD/Article: covered only for listed ICD-10
    "modifier_required",
    "frequency_limit",
    "place_of_service",
    "documentation_required",
]

Action = Literal["DENY", "PEND", "PAY", "REQUIRE_DOC"]

ReviewStatus = Literal["PENDING", "NEEDS_REVIEW", "APPROVED", "REJECTED"]


# --------------------------------------------------------------------------
# Layer 1 — LLM-facing (strict-mode compatible)
# --------------------------------------------------------------------------

class ExtractedRule(BaseModel):
    """What the model is allowed to emit. Deliberately flat and permissive:
    every constraint we care about is enforced afterwards in validate.py,
    where failures are observable and countable rather than silent."""

    rule_type: RuleType
    trigger_codes: list[str]          # CPT/HCPCS that activate the rule
    conflicting_codes: list[str]      # for code_pair_conflict
    covered_icd10: list[str]          # for diagnosis_coverage
    max_units: Optional[int]          # for unit_limit
    required_modifiers: list[str]
    place_of_service: list[str]
    action: Action
    human_readable_reason: str
    effective_date: Optional[str]     # "YYYY-MM-DD" — string, not date
    end_date: Optional[str]
    quoted_span: str                  # MUST be verbatim from source text
    confidence: float                 # 0.0–1.0, range checked downstream


class ExtractionResult(BaseModel):
    """Top-level object returned by the extraction call."""
    rules: list[ExtractedRule]


# --------------------------------------------------------------------------
# Layer 2 — internal domain model
# --------------------------------------------------------------------------

class Provenance(BaseModel):
    """Every rule must be traceable to a specific span of a specific version
    of a specific document. A rule that cannot cite its source cannot be
    defended in an appeal, so this is required, not optional."""

    source_doc_id: str                # "L34578", "A56789", "NCCI-Ch1"
    source_doc_title: str
    source_version: str               # MCD version field, or file quarter
    char_start: int
    char_end: int
    quoted_span: str
    retrieved_at: str                 # ISO-8601 UTC


class RuleSpec(BaseModel):
    rule_id: str
    rule_type: RuleType

    trigger_codes: list[str] = []
    conflicting_codes: list[str] = []
    covered_icd10: list[str] = []
    max_units: Optional[int] = None
    required_modifiers: list[str] = []
    place_of_service: list[str] = []

    action: Action
    human_readable_reason: str

    effective_date: Optional[date] = None
    end_date: Optional[date] = None

    # governance metadata
    provenance: Provenance
    extraction_model: str
    prompt_version: str
    confidence: float = Field(ge=0.0, le=1.0)
    review_status: ReviewStatus = "PENDING"
    validation_issues: list[str] = []

    @field_validator("trigger_codes", "conflicting_codes", "covered_icd10", mode="before")
    @classmethod
    def _upper_strip(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [str(x).strip().upper() for x in v if str(x).strip()]
        return v

    def is_executable(self) -> bool:
        """Only approved rules ever reach the adjudication engine."""
        return self.review_status == "APPROVED"


# --------------------------------------------------------------------------
# Claims (minimal shape needed by the engine)
# --------------------------------------------------------------------------

class ClaimLine(BaseModel):
    line_id: str
    cpt_hcpcs: str
    units: int = 1
    modifiers: list[str] = []
    charge_amount: float = 0.0


class Claim(BaseModel):
    claim_id: str
    member_id: str
    date_of_service: date
    place_of_service: Optional[str] = None
    diagnosis_codes: list[str] = []
    lines: list[ClaimLine] = []


class Decision(BaseModel):
    claim_id: str
    line_id: Optional[str]
    action: Action
    rule_id: str
    reason: str
    citation: str          # "A56789 v.R12 chars 1204-1361"


# --------------------------------------------------------------------------
# Strict-mode schema transformation
# --------------------------------------------------------------------------

_STRIP_KEYWORDS = (
    "default", "format", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern",
    "minItems", "maxItems", "uniqueItems", "examples",
)


def to_strict_schema(schema: dict) -> dict:
    """Convert a Pydantic-generated JSON Schema into OpenAI strict form.

    Strict mode requires:
      * every object closed with additionalProperties: false
      * every property present in `required`
      * none of the keywords in _STRIP_KEYWORDS

    Pydantic emits several of those by default, so passing
    model_json_schema() straight to the API fails. This walks the tree
    (including $defs) and normalises it.
    """
    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            for kw in _STRIP_KEYWORDS:
                node.pop(kw, None)
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}).keys())
            for value in list(node.values()):
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        return node

    return walk(copy.deepcopy(schema))


def response_format_for(model_cls: type[BaseModel], name: str) -> dict:
    """Build the `response_format` payload for chat.completions.create()."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": to_strict_schema(model_cls.model_json_schema()),
        },
    }


# --------------------------------------------------------------------------
# Promotion: untrusted -> trusted
# --------------------------------------------------------------------------

def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def make_rule_id(doc_id: str, quoted_span: str, rule_type: str) -> str:
    """Deterministic ID: same span + same rule type -> same ID across runs.
    Makes reruns diffable instead of producing a fresh set of UUIDs."""
    digest = hashlib.sha256(
        f"{doc_id}|{rule_type}|{quoted_span}".encode("utf-8")
    ).hexdigest()[:10]
    return f"R-{digest}"


def promote(
    extracted: ExtractedRule,
    *,
    doc_id: str,
    doc_title: str,
    doc_version: str,
    char_start: int,
    char_end: int,
    extraction_model: str,
    prompt_version: str,
    validation_issues: list[str],
    review_status: ReviewStatus,
) -> RuleSpec:
    """Turn a validated ExtractedRule into a domain RuleSpec.

    Callers must run validate.validate_extracted_rule() first and pass its
    verdict in; promote() does not decide trust, it records it.
    """
    return RuleSpec(
        rule_id=make_rule_id(doc_id, extracted.quoted_span, extracted.rule_type),
        rule_type=extracted.rule_type,
        trigger_codes=extracted.trigger_codes,
        conflicting_codes=extracted.conflicting_codes,
        covered_icd10=extracted.covered_icd10,
        max_units=extracted.max_units,
        required_modifiers=extracted.required_modifiers,
        place_of_service=extracted.place_of_service,
        action=extracted.action,
        human_readable_reason=extracted.human_readable_reason,
        effective_date=_parse_date(extracted.effective_date),
        end_date=_parse_date(extracted.end_date),
        provenance=Provenance(
            source_doc_id=doc_id,
            source_doc_title=doc_title,
            source_version=doc_version,
            char_start=char_start,
            char_end=char_end,
            quoted_span=extracted.quoted_span,
            retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
        extraction_model=extraction_model,
        prompt_version=prompt_version,
        confidence=max(0.0, min(1.0, float(extracted.confidence))),
        review_status=review_status,
        validation_issues=validation_issues,
    )


if __name__ == "__main__":
    print(json.dumps(response_format_for(ExtractionResult, "extraction_result"), indent=2))
