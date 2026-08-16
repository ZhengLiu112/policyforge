# PolicyForge

**Turning written healthcare payment policy into auditable, executable rules — with a citation for every decision.**

> Submitted as the proof-of-concept for the Cotiviti Generative AI Research Engineer internship assessment (Topic 3: Content Management in Health Care).

---

## The Problem

Payer coverage and coding policy is published as prose. Claims engines execute deterministic rules. Every quarter, someone reads the new NCCI edit files and revised Local Coverage Determinations by hand, writes rule specifications, hands them to engineering, and waits. The translation is slow, expensive, and not reproducible — and it starts over every quarter.

## What This Does

- **Extract** — turns policy narrative into a structured `RuleSpec`, where every field carries a character-level citation back to the source document
- **Diff** — compares two policy versions, separates material change from wording change, and quantifies the claims impact of the delta
- **Govern** — routes every generated rule through validation and a human approval queue before it becomes executable

**Design commitment: the LLM proposes, a deterministic engine disposes.**
The model participates in rule *authoring*. It is not in the adjudication path. Adjudication is compiled Python, reproducible and auditable.

## Results

| Configuration | HCPC F1 | Std Dev (σ) | Halluc. rate | What it buys |
|---|---|---|---|---|
| L0  bare prompt | 0.556 | 0.091 | 66.7% | baseline |
| L1  + strict schema | 0.556 | 0.054 | 0.0% | eliminates hallucinations |
| L2  + retrieval | 0.556 | 0.057 | 0.0% | terminology grounding |
| L3  + validator | 0.445 | 0.044 | 0.0% | precision over recall |
| L4  + few-shot | 0.667 | 0.054 | 0.0% | best accuracy |

Evaluated on 3 real MCD Billing & Coding articles against the official CMS code tables (external ground truth, not self-labelled). Each level run 5× at T=0.7 with caching disabled for variance analysis.

**Key findings:**
- Structured output dropped hallucination rate from 66.7% to 0% (L0→L1)
- Structured output also halved run-to-run variance (σ 0.091→0.054) — predictability matters as much as accuracy for a payer system
- PLA molecular/genomic codes are 24% of Q2→Q3 added edits but drive 47% of estimated new denial impact ($591K)
- Structural diff matches the official CMS Quarterly Additions/Deletions/Revisions file exactly across 2,199 additions and 7 modifier changes

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add your OPENAI_API_KEY
python3 tests/test_validate.py
streamlit run app.py
```

## Architecture

```
INGEST → EXTRACT (LLM) → VALIDATE → REVIEW QUEUE → COMPILE → ADJUDICATE
                              ↓
                          DISCARDED
                    (hallucinated citation)
```

| Module | Role |
|---|---|
| `src/schema.py` | Two-layer data contract: LLM-facing strict schema + internal domain model |
| `src/validate.py` | Deterministic hallucination gate — no model, no network |
| `src/llm.py` | OpenAI Structured Outputs wrapper with caching and usage accounting |
| `src/extract.py` | Five ablation levels: L0 bare prompt → L4 few-shot |
| `src/ncci.py` | NCCI PTP edit file loader — handles CMS multi-line preamble, all 4 fragments |
| `src/engine.py` | Claims adjudication. Deterministic, no LLM |
| `src/diff.py` | Policy version comparison and business impact ranking |
| `src/mcd.py` | MCD Article/LCD loader with HTML cleaning and code table linkage |
| `src/impact.py` | Ranks newly added edits by estimated denial exposure |

### Key design decisions

1. **Two schemas, not one.** OpenAI strict mode rejects `default`, `minimum`, `pattern`, and `format`. `to_strict_schema()` normalises the Pydantic output; `promote()` is the single point where an untrusted model output becomes a trusted domain object.

2. **The citation is checked, not trusted.** Every rule must quote its source verbatim. `find_span()` verifies the quote is really in the document, tolerating whitespace re-wrapping but nothing else. A quote that cannot be located is counted as a hallucination — making the hallucination rate a continuous metric requiring zero human annotation.

3. **Deterministic rule IDs.** `sha256(doc_id | rule_type | quoted_span)` means rerunning extraction produces the same IDs, so successive runs are diffable rather than generating fresh UUIDs each time.

## AI Governance

Every extracted rule passes through three gates before it can execute:

1. **Validator** — rejects rules whose `quoted_span` cannot be found verbatim in the source document
2. **Confidence threshold** — rules below 0.70 are routed to `NEEDS_REVIEW`, not executed
3. **Human approval queue** — only `APPROVED` rules are compiled into the adjudication engine

An append-only audit log records every extraction, validation, and approval event with timestamp, model version, and prompt version. Maps to NIST AI RMF: GOVERN (role separation and human oversight), MEASURE (hallucination rate, F1, σ), MANAGE (version-pinned rule lineage).

## Data Sources & Licensing

All inputs are public CMS data. CPT codes are used as identifiers only; CPT descriptor text is copyright AMA and is not reproduced in this repository. See `DATA_ACQUISITION.md` for download instructions.

| Source | Used for |
|---|---|
| NCCI PTP Edits v321r0 / v322r0 (Practitioner) | Rule index, version diff ground truth |
| MCD Current Article Data | Extraction input + evaluation ground truth |
| NCCI Policy Manual 2026 | Retrieval context (L2/L3/L4) |

## Limitations

- Evaluation covers 3 articles (~30 rules) — directional, not population-level
- Official code tables may include codes inherited from a parent LCD and absent from the narrative, penalising recall for reasons unrelated to model quality
- Charge bands in impact analysis are order-of-magnitude estimates, not fee-schedule data
- No cross-model comparison; variance analysis uses a single sampling temperature

## Deliverables

- `docs/PolicyForge_Report.docx` — written report (2 pages + references, APA)
- `docs/PolicyForge.pptx` — slide deck (13 slides)
- `PolicyForge_demo.mp4` — five-minute walkthrough with live demo
- `Resume.pdf`
