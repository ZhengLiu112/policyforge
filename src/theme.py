"""Visual theme for the PolicyForge console.

The subject is a payment-integrity engineering tool — an audit surface,
not a marketing page. The look borrows from the world it serves: dense,
monospaced, ledger-like, with a single amber accent reserved for the one
thing that matters most in this product — the cited span of source text
that justifies every rule.

Palette (deep ink navy + restrained amber), deliberately away from the
warm-cream / terracotta default:
  ink        #0d1117  page background
  slate      #161b22  panel background
  border     #21262d  hairlines
  text       #c9d1d9  body
  muted      #6e7681  captions
  amber      #e3b341  the accent — citations, key figures
  green      #3fb950  PAY / pass
  red        #f85149  DENY / reject
  blue       #58a6ff  links, neutral highlights
"""

THEME_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700&display=swap');

:root {
  --ink:    #0d1117;
  --slate:  #161b22;
  --border: #21262d;
  --text:   #c9d1d9;
  --muted:  #6e7681;
  --amber:  #e3b341;
  --green:  #3fb950;
  --red:    #f85149;
  --blue:   #58a6ff;
}

.stApp { background: var(--ink); color: var(--text); }

/* kill the default streamlit chrome */
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding-top: 2.5rem; max-width: 1100px; }

/* typography */
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
code, pre, .mono { font-family: 'JetBrains Mono', monospace; }

h1, h2, h3 { color: #f0f6fc; font-weight: 700; letter-spacing: -0.01em; }

/* masthead */
.masthead {
  border-bottom: 1px solid var(--border);
  padding-bottom: 1.2rem; margin-bottom: 1.8rem;
}
.masthead .wordmark {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1.55rem; font-weight: 700; color: #f0f6fc;
  letter-spacing: -0.02em;
}
.masthead .wordmark .fg { color: var(--amber); }
.masthead .tag {
  color: var(--muted); font-size: 0.9rem; margin-top: 0.25rem;
}

/* metric cards */
.metric-row { display: flex; gap: 0.9rem; margin: 1rem 0 1.6rem; }
.metric {
  flex: 1; background: var(--slate); border: 1px solid var(--border);
  border-radius: 8px; padding: 1rem 1.1rem;
}
.metric .val {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1.65rem; font-weight: 700; color: #f0f6fc; line-height: 1.1;
}
.metric .val.amber { color: var(--amber); }
.metric .val.green { color: var(--green); }
.metric .lbl {
  color: var(--muted); font-size: 0.78rem; text-transform: uppercase;
  letter-spacing: 0.06em; margin-top: 0.35rem;
}

/* the signature element: cited source text with the span highlighted */
.source-panel {
  background: var(--slate); border: 1px solid var(--border);
  border-radius: 8px; padding: 1.2rem 1.3rem;
  font-family: 'JetBrains Mono', monospace; font-size: 0.82rem;
  line-height: 1.7; color: #8b949e; white-space: pre-wrap;
  max-height: 340px; overflow-y: auto;
}
.source-panel mark {
  background: rgba(227, 179, 65, 0.18);
  color: var(--amber); padding: 0.05em 0.15em;
  border-bottom: 1.5px solid var(--amber); border-radius: 2px;
}

/* rule cards */
.rule {
  background: var(--slate); border: 1px solid var(--border);
  border-left: 3px solid var(--amber); border-radius: 6px;
  padding: 0.85rem 1rem; margin-bottom: 0.7rem;
}
.rule .rtype {
  font-family: 'JetBrains Mono', monospace; font-size: 0.72rem;
  color: var(--amber); text-transform: uppercase; letter-spacing: 0.05em;
}
.rule .codes { font-family: 'JetBrains Mono', monospace; font-size: 0.95rem;
  color: #f0f6fc; margin: 0.3rem 0; }
.rule .reason { color: var(--text); font-size: 0.85rem; }
.rule .cite {
  color: var(--muted); font-size: 0.75rem; margin-top: 0.4rem;
  font-family: 'JetBrains Mono', monospace;
}
.rule .conf { float: right; font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem; color: var(--muted); }

/* badges */
.badge { display: inline-block; padding: 0.12em 0.55em; border-radius: 4px;
  font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; font-weight: 500; }
.badge.pay   { background: rgba(63,185,80,0.15);  color: var(--green); }
.badge.deny  { background: rgba(248,81,73,0.15);  color: var(--red); }
.badge.pend  { background: rgba(227,179,65,0.15); color: var(--amber); }
.badge.pass  { background: rgba(63,185,80,0.15);  color: var(--green); }
.badge.review{ background: rgba(227,179,65,0.15); color: var(--amber); }
.badge.reject{ background: rgba(248,81,73,0.15);  color: var(--red); }

/* tabs */
.stTabs [data-baseweb="tab-list"] { gap: 0.3rem; border-bottom: 1px solid var(--border); }
.stTabs [data-baseweb="tab"] {
  background: transparent; color: var(--muted);
  font-size: 0.9rem; font-weight: 500; padding: 0.5rem 0.9rem;
}
.stTabs [aria-selected="true"] { color: var(--amber) !important; }

/* buttons */
.stButton button {
  background: var(--slate); color: var(--text);
  border: 1px solid var(--border); border-radius: 6px;
  font-weight: 500; font-size: 0.85rem;
}
.stButton button:hover { border-color: var(--amber); color: var(--amber); }

/* dataframes */
.stDataFrame { border: 1px solid var(--border); border-radius: 8px; }

/* selectbox / text area */
.stSelectbox div[data-baseweb="select"] > div,
.stTextArea textarea {
  background: var(--slate); border-color: var(--border); color: var(--text);
}

.section-label {
  font-family: 'JetBrains Mono', monospace; font-size: 0.75rem;
  color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em;
  margin: 1.4rem 0 0.6rem;
}
</style>
"""


def masthead(subtitle: str) -> str:
    return f"""
<div class="masthead">
  <div class="wordmark">Policy<span class="fg">Forge</span></div>
  <div class="tag">{subtitle}</div>
</div>
"""


def metric_card(value: str, label: str, tone: str = "") -> str:
    return f'<div class="metric"><div class="val {tone}">{value}</div>' \
           f'<div class="lbl">{label}</div></div>'


def metric_row(cards: list[str]) -> str:
    return '<div class="metric-row">' + "".join(cards) + "</div>"


def highlight_source(text: str, start: int, end: int) -> str:
    """Wrap [start:end] of text in a <mark> for the source panel."""
    import html
    if start < 0 or end <= start or end > len(text):
        return f'<div class="source-panel">{html.escape(text)}</div>'
    before = html.escape(text[:start])
    span = html.escape(text[start:end])
    after = html.escape(text[end:])
    return f'<div class="source-panel">{before}<mark>{span}</mark>{after}</div>'
