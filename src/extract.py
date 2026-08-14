"""
PolicyForge — policy text to RuleSpec.

Four extraction levels. They exist so the ablation study measures
something real rather than reporting a single number with no baseline:

  L0  bare prompt, free-form JSON            (baseline)
  L1  + strict Structured Outputs schema     (format is guaranteed)
  L2  + retrieved NCCI Policy Manual context (terminology grounding)
  L3  + deterministic validator and abstain  (trust is gated)

Only L3 is the shipped configuration. L0-L2 exist to show what each
layer buys, which is the difference between "I used an LLM" and "I made
engineering choices I can defend."
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Literal, Optional

from .llm import LLMClient
from .schema import ExtractedRule, ExtractionResult, RuleSpec, promote
from .validate import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    ValidationReport,
    validate_extracted_rule,
)

Level = Literal["L0", "L1", "L2", "L3"]
PROMPT_VERSION = "v3"


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

_SYSTEM_L0 = (
    "You convert healthcare payment policy text into structured rules. "
    "Return JSON."
)

_SYSTEM_STRUCTURED = """\
You are a payment-policy analyser for a healthcare payer. You convert \
written coverage and coding policy into structured rule specifications \
that a deterministic claims engine will later execute.

Hard requirements:

1. Extract only rules that the supplied policy text states directly. Do \
   not add rules from your own knowledge of Medicare policy, and do not \
   generalise beyond what is written.
2. `quoted_span` MUST be a verbatim substring of the supplied policy \
   text — copy it character for character. It is checked programmatically \
   against the source. If you cannot quote the text supporting a rule, do \
   not emit that rule.
3. Populate a field only when the text supports it. Use null or an empty \
   list otherwise. Do not guess codes.
4. `confidence` reflects how unambiguously the text supports the rule: \
   1.0 for an explicit, unqualified statement; below 0.7 when the text is \
   vague, conditional on information not supplied, or open to more than \
   one reading.
5. Dates must be YYYY-MM-DD.
6. Codes: CPT/HCPCS are 5 characters (99213, J0911, 0468U). ICD-10-CM \
   keeps its decimal point (G56.00). Modifiers are two characters (59).

Emit one rule per distinct, separately-enforceable requirement. A \
paragraph containing a diagnosis restriction and a unit limit yields two \
rules, not one.

If the text contains no enforceable payment rule, return an empty list. \
An empty result is a correct answer."""

_RETRIEVAL_BLOCK = """\

Reference context (NCCI Policy Manual excerpts). Use this ONLY to \
interpret terminology and coding conventions. It is NOT a source of \
rules — every rule you emit must be quotable from the policy text above.

{context}"""

_USER_TEMPLATE = """\
Document ID: {doc_id}
Document title: {doc_title}
Version: {doc_version}

--- POLICY TEXT ---
{policy_text}
--- END POLICY TEXT ---
"""


def build_messages(
    level: Level,
    *,
    doc_id: str,
    doc_title: str,
    doc_version: str,
    policy_text: str,
    context: str = "",
) -> tuple[str, str]:
    system = _SYSTEM_L0 if level == "L0" else _SYSTEM_STRUCTURED
    user = _USER_TEMPLATE.format(
        doc_id=doc_id,
        doc_title=doc_title,
        doc_version=doc_version,
        policy_text=policy_text,
    )
    if level in ("L2", "L3") and context:
        user += _RETRIEVAL_BLOCK.format(context=context)
    return system, user


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

@dataclass
class ExtractionOutcome:
    level: Level
    rules: list[RuleSpec]              # promoted, with provenance
    reports: list[ValidationReport]    # one per candidate, including rejects
    raw: list[ExtractedRule]           # everything the model emitted
    usage: dict[str, Any]

    @property
    def rejected(self) -> int:
        return sum(r.verdict == "REJECT" for r in self.reports)


def extract_rules(
    client: LLMClient,
    *,
    level: Level,
    doc_id: str,
    doc_title: str,
    doc_version: str,
    policy_text: str,
    retriever: Optional[Callable[[str], Iterable[str]]] = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> ExtractionOutcome:
    """Run one extraction at one ablation level.

    L0-L2 keep every candidate the model produced, so their metrics show
    what unfiltered output looks like. L3 drops REJECTs and marks the rest
    PENDING or NEEDS_REVIEW for the approval queue — nothing here is
    executable until a human approves it.
    """
    context = ""
    if level in ("L2", "L3") and retriever is not None:
        context = "\n\n".join(retriever(policy_text))

    system, user = build_messages(
        level,
        doc_id=doc_id,
        doc_title=doc_title,
        doc_version=doc_version,
        policy_text=policy_text,
        context=context,
    )

    result = client.parse(
        system=system,
        user=user,
        model_cls=ExtractionResult,
        schema_name="extraction_result",
    )

    reports: list[ValidationReport] = []
    promoted: list[RuleSpec] = []

    for candidate in result.rules:
        report = validate_extracted_rule(
            candidate, policy_text, confidence_threshold=confidence_threshold
        )
        reports.append(report)

        # L3 is the only level that acts on the verdict. The others promote
        # everything so the ablation can measure what they would have let
        # through.
        if level == "L3" and report.verdict == "REJECT":
            continue

        span = report.span
        promoted.append(
            promote(
                candidate,
                doc_id=doc_id,
                doc_title=doc_title,
                doc_version=doc_version,
                char_start=span.start if span else -1,
                char_end=span.end if span else -1,
                extraction_model=client.model,
                prompt_version=PROMPT_VERSION if level != "L0" else "v0",
                validation_issues=report.issues,
                review_status=(
                    "NEEDS_REVIEW" if report.verdict == "NEEDS_REVIEW" else "PENDING"
                ),
            )
        )

    return ExtractionOutcome(
        level=level,
        rules=promoted,
        reports=reports,
        raw=list(result.rules),
        usage=client.usage.as_dict(client.model),
    )
