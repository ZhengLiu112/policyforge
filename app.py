"""PolicyForge console — the demo surface for the whole pipeline.

Four tabs, one per capability:
  Extract   policy narrative -> rules, with the cited span highlighted
  Diff      quarter-over-quarter edit changes and their business impact
  Adjudicate  run claims through the deterministic engine
  Evaluate  the ablation and variance results

Data loads are cached so the walkthrough never waits on a cold read.
The LLM calls in Extract are cached on disk (see llm.py), so a recorded
demo replays instantly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv()

from src.theme import (
    THEME_CSS, masthead, metric_card, metric_row, highlight_source,
)

INBOX = Path("data/inbox")
POLICIES = Path("data/policies")

st.set_page_config(page_title="PolicyForge", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown(THEME_CSS, unsafe_allow_html=True)


# --------------------------------------------------------------------------
# cached loaders
# --------------------------------------------------------------------------

@st.cache_resource(show_spinner="Loading coverage articles…")
def get_articles():
    from src.mcd import load_articles, pick_eval_articles
    arts = load_articles(POLICIES)
    picks = pick_eval_articles(arts, n=3)
    return arts, picks


@st.cache_resource(show_spinner="Loading NCCI quarters…")
def get_quarters():
    from src.ncci import active_edits, find_quarter_fragments, load_ptp_quarter
    old_df = load_ptp_quarter(find_quarter_fragments(INBOX, "v321r0"))
    new_df = load_ptp_quarter(find_quarter_fragments(INBOX, "v322r0"))
    return old_df, new_df


@st.cache_resource(show_spinner="Computing quarterly diff…")
def get_impact():
    from src.impact import summarize
    return summarize("v321r0", "v322r0", INBOX)


def load_md(path: str) -> str:
    p = Path(path)
    return p.read_text(encoding="utf-8") if p.exists() else "_Not generated yet._"


# --------------------------------------------------------------------------
# header
# --------------------------------------------------------------------------

st.markdown(masthead(
    "Turning written healthcare payment policy into auditable, "
    "executable rules — with a citation for every decision."
), unsafe_allow_html=True)

tab_x, tab_d, tab_a, tab_e = st.tabs(
    ["  Extract  ", "  Diff & Impact  ", "  Adjudicate  ", "  Evaluate  "]
)


# ==========================================================================
# TAB 1 — Extract
# ==========================================================================

with tab_x:
    articles, picks = get_articles()

    st.markdown('<div class="section-label">Source policy</div>',
                unsafe_allow_html=True)
    labels = {f"[{a.article_id}] {a.title[:60]}": a for a in picks}
    choice = st.selectbox("article", list(labels.keys()),
                          label_visibility="collapsed")
    article = labels[choice]

    c1, c2 = st.columns([3, 2])
    with c1:
        level = st.select_slider(
            "Extraction level",
            options=["L0", "L1", "L2", "L3", "L4"],
            value="L4",
            help="L0 bare prompt · L1 structured · L2 +retrieval · "
                 "L3 +validator · L4 +few-shot",
        )
    with c2:
        run = st.button("Extract rules", type="primary", use_container_width=True)

    st.markdown(metric_row([
        metric_card(f"{len(article.hcpc_codes)}", "HCPCS in answer key"),
        metric_card(f"{len(article.icd10_covered)}", "ICD-10 covered"),
        metric_card(f"{len(article.narrative):,}", "narrative chars"),
    ]), unsafe_allow_html=True)

    if run:
        from src.llm import LLMClient
        from src.extract import extract_rules
        client = LLMClient(use_cache=True)
        with st.spinner(f"Extracting at {level}…"):
            outcome = extract_rules(
                client, level=level,
                doc_id=article.article_id, doc_title=article.title,
                doc_version=article.version, policy_text=article.narrative,
            )
        st.session_state["outcome"] = outcome
        st.session_state["article"] = article

    outcome = st.session_state.get("outcome")
    if outcome and st.session_state.get("article") is article:
        gold = article.hcpc_codes
        pred = set()
        for r in outcome.rules:
            pred.update(c.upper() for c in r.trigger_codes)
            pred.update(c.upper() for c in r.conflicting_codes)
        tp = len(pred & gold)
        prec = tp / len(pred) if pred else 0
        rec = tp / len(gold) if gold else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0
        halluc = sum(rep.hallucinated for rep in outcome.reports)

        st.markdown(metric_row([
            metric_card(f"{f1:.2f}", "HCPCS F1", "amber"),
            metric_card(f"{len(outcome.rules)}", "rules extracted"),
            metric_card(f"{halluc}", "hallucinated", "green" if halluc == 0 else ""),
        ]), unsafe_allow_html=True)

        left, right = st.columns([1, 1])
        with left:
            st.markdown('<div class="section-label">Extracted rules</div>',
                        unsafe_allow_html=True)
            for r in outcome.rules:
                codes = " · ".join(r.trigger_codes) or "—"
                icd = (" → " + ", ".join(r.covered_icd10)) if r.covered_icd10 else ""
                st.markdown(f"""
<div class="rule">
  <span class="conf">conf {r.confidence:.2f}</span>
  <div class="rtype">{r.rule_type}</div>
  <div class="codes">{codes}{icd}</div>
  <div class="reason">{r.human_readable_reason}</div>
  <div class="cite">▸ {r.provenance.quoted_span[:90]}</div>
</div>""", unsafe_allow_html=True)

        with right:
            st.markdown('<div class="section-label">Source, with first '
                        'citation highlighted</div>', unsafe_allow_html=True)
            if outcome.rules:
                r0 = outcome.rules[0]
                st.markdown(
                    highlight_source(article.narrative,
                                     r0.provenance.char_start,
                                     r0.provenance.char_end),
                    unsafe_allow_html=True)
            else:
                st.markdown(highlight_source(article.narrative, -1, -1),
                            unsafe_allow_html=True)
    else:
        st.markdown('<div class="section-label">Source narrative</div>',
                    unsafe_allow_html=True)
        st.markdown(highlight_source(article.narrative, -1, -1),
                    unsafe_allow_html=True)


# ==========================================================================
# TAB 2 — Diff & Impact
# ==========================================================================

with tab_d:
    out = get_impact()
    diff, ranked, by_family = out["diff"], out["ranked"], out["by_family"]

    st.markdown('<div class="section-label">v321r0 → v322r0 · '
                'Practitioner PTP edits</div>', unsafe_allow_html=True)

    total_denied = ranked["denied_charge_usd"].sum() if not ranked.empty else 0
    st.markdown(metric_row([
        metric_card(f"+{diff.n_added:,}", "edits added"),
        metric_card(f"{diff.n_deleted:,}", "deleted"),
        metric_card(f"{diff.n_modifier_changed}", "modifier changes"),
        metric_card(f"${total_denied:,.0f}", "est. new denials", "amber"),
    ]), unsafe_allow_html=True)

    st.caption("Structural diff matches the official CMS Quarterly "
               "Additions/Deletions/Revisions file exactly.")

    if not by_family.empty:
        st.markdown('<div class="section-label">Where the impact lands, '
                    'by code family</div>', unsafe_allow_html=True)
        import plotly.express as px
        fig = px.bar(
            by_family, x="family", y="total_denied_usd",
            text="share_of_impact",
            color="family",
            color_discrete_map={"PLA": "#e3b341", "CPT": "#58a6ff",
                                "CAT3": "#3fb950", "HCPCS": "#6e7681"},
        )
        fig.update_traces(texttemplate="%{text:.0%}", textposition="outside")
        fig.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font_color="#c9d1d9", showlegend=False, height=320,
            margin=dict(l=0, r=0, t=10, b=0),
            xaxis_title=None, yaxis_title="est. denied $",
        )
        fig.update_xaxes(gridcolor="#21262d")
        fig.update_yaxes(gridcolor="#21262d")
        st.plotly_chart(fig, use_container_width=True)

        pla = by_family[by_family["family"] == "PLA"]
        if not pla.empty:
            share = pla.iloc[0]["share_of_impact"]
            n = int(pla.iloc[0]["edits_added"])
            st.markdown(
                f"**Reviewer takeaway.** Molecular/genomic (PLA) codes are "
                f"{n} of {diff.n_added:,} added edits ({n/diff.n_added:.0%}) "
                f"but drive **{share:.0%}** of the new denial dollars — the "
                f"highest-yield place to focus limited review capacity.")

    st.markdown('<div class="section-label">Highest-impact added edits '
                '(reviewer worklist)</div>', unsafe_allow_html=True)
    st.dataframe(ranked.head(15), use_container_width=True, hide_index=True)


# ==========================================================================
# TAB 3 — Adjudicate
# ==========================================================================

with tab_a:
    old_df, new_df = get_quarters()
    from src.ncci import active_edits
    from src.engine import (PTPIndex, adjudicate_batch, generate_test_claims)

    index = PTPIndex.from_dataframe(active_edits(new_df), "v322r0")

    st.markdown('<div class="section-label">Synthetic claims through the '
                'deterministic engine</div>', unsafe_allow_html=True)
    st.caption(f"{len(index):,} active PTP rules loaded · no LLM in this path")

    if st.button("Run adjudication", type="primary"):
        claims = generate_test_claims(index)
        results = adjudicate_batch(claims, index)
        charge = {(c.claim_id, ln.line_id): ln.charge_amount
                  for c in claims for ln in c.lines}
        results["charge"] = [charge.get((r.claim_id, r.line_id), 0)
                             for r in results.itertuples(index=False)]
        st.session_state["adj"] = (results, len(claims))

    adj = st.session_state.get("adj")
    if adj:
        results, n_claims = adj
        pay = (results["action"] == "PAY").sum()
        deny = (results["action"] == "DENY").sum()
        denied_amt = results[results["action"] == "DENY"]["charge"].sum()
        st.markdown(metric_row([
            metric_card(f"{pay}", "paid", "green"),
            metric_card(f"{deny}", "denied", ""),
            metric_card(f"${denied_amt:,.0f}", "denied charges", "amber"),
        ]), unsafe_allow_html=True)

        show = results[["claim_id", "code", "action", "rule_pair", "reason"]].head(12)
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.caption("Every denial carries the exact NCCI edit that produced "
                   "it — the audit trail a payer needs on appeal.")


# ==========================================================================
# TAB 4 — Evaluate
# ==========================================================================

with tab_e:
    st.markdown('<div class="section-label">Ablation · does each layer '
                'earn its place?</div>', unsafe_allow_html=True)
    st.markdown(load_md("eval/results.md"))

    st.markdown('<div class="section-label">Variance · is the number '
                'stable or lucky?</div>', unsafe_allow_html=True)
    st.markdown(load_md("eval/variance.md"))
