# PolicyForge

**Turning written healthcare payment policy into auditable, executable rules — with a citation for every decision.**

> Submitted as the hackathon proof-of-concept for the Cotiviti Generative AI
> Research Engineer internship assessment (Topic 3: Content Management in
> Health Care).

---

## The Problem

Payer coverage and coding policy is published as prose. Claims engines
execute deterministic rules. Every quarter, someone reads the new NCCI
edit files and revised Local Coverage Determinations by hand, writes rule
specifications, hands them to engineering, and waits. The translation is
slow, expensive, and not reproducible — and it starts over every quarter.

## What This Does

- **Extract** — turns policy narrative into a structured `RuleSpec`, where
  every field carries a character-level citation back to the source document
- **Diff** — compares two policy versions, separates material change from
  wording change, and quantifies the claims impact of the delta
- **Govern** — routes every generated rule through validation and a human
  approval queue before it becomes executable

**Design commitment: the LLM proposes, a deterministic engine disposes.**
The model participates in rule *authoring*. It is not in the adjudication
path. Adjudication is compiled Python, reproducible and auditable.

## Results

_Populated in Phase 2 — ablation table (L0/L1/L2/L3), extraction F1,
hallucination rate, cost per document._

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your OPENAI_API_KEY
python3 tests/test_validate.py
streamlit run app.py
```

## Architecture

_Diagram added in Phase 4._

| Module | Role |
|---|---|
| `src/schema.py` | Two-layer data contract: LLM-facing strict schema + internal domain model |
| `src/validate.py` | Deterministic validation — the hallucination gate. No model, no network |
| `src/llm.py` | OpenAI Structured Outputs wrapper with caching and usage accounting |
| `src/extract.py` | Four ablation levels: bare prompt → strict schema → retrieval → validator |
| `src/compile.py` | `RuleSpec` → executable Python predicate |
| `src/engine.py` | Claims adjudication. Deterministic, no LLM |
| `src/diff.py` | Policy version comparison and impact analysis |
| `src/govern.py` | Approval queue and append-only audit log |

### Key design decisions

1. **Two schemas, not one.** OpenAI strict mode rejects `default`,
   `minimum`, `pattern`, and `format`, and requires every property to be
   listed in `required`. A Pydantic model rich enough to validate a rule
   before execution cannot be sent to the API as-is. `to_strict_schema()`
   normalises it; `promote()` is the single point where an untrusted model
   output becomes a trusted domain object.

2. **The citation is checked, not trusted.** Every rule must quote its
   source verbatim. `find_span()` verifies the quote is really in the
   document, tolerating whitespace re-wrapping but nothing else. A quote
   that cannot be located is counted as a hallucination — which makes the
   hallucination rate a continuous metric requiring zero human annotation.

3. **Deterministic rule IDs.** `sha256(doc_id | rule_type | quoted_span)`
   means rerunning extraction produces the same IDs, so successive runs are
   diffable instead of generating a fresh set of UUIDs each time.

## AI Governance

_Expanded in Phase 4 — approval queue, audit log, abstain policy,
NIST AI RMF mapping._

## Data Sources & Licensing

See `data/DATA_SOURCES.md`. All inputs are public CMS data. CPT codes are
used as identifiers only; CPT descriptor text is copyright AMA and is not
reproduced in this repository.

## Limitations

_Stated honestly in Phase 2, alongside the results._

## Deliverables

- `docs/Report.docx` — written report
- `docs/Presentation.pptx` — slide deck
- `demo.mp4` — five-minute walkthrough
- `Resume.pdf`
