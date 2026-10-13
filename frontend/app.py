"""Tether chat UI (cyberpunk / glitch theme).

Two ways to run it:
- With the FastAPI server: set BACKEND_URL (e.g. http://127.0.0.1:8000) and
  the app talks to the API over HTTP. The container image does this.
- On its own (Streamlit Community Cloud, or quick local runs): leave
  BACKEND_URL unset and the app calls backend/service.py directly.

Run locally (from the tether/ folder):
    python -m streamlit run frontend/app.py
"""

import html
import os
import sys
import time
import uuid
from pathlib import Path

import requests
import streamlit as st

# Make the repo root importable so "backend" resolves when Streamlit runs
# this file from the frontend/ folder.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # local runs read .env; on Streamlit Cloud the secrets are env vars already
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

BACKEND_URL = os.environ.get("BACKEND_URL")  # unset = embedded mode

MODEL = "gpt-5-mini"
PLATFORM = "Microsoft Foundry on Azure"
RATE_LIMIT = "5 questions per minute, for the public demo"

# Where the project is now and where it's heading, shown in the sidebar.
ROADMAP = [
    ("Now", "A FastAPI and Streamlit web app calling a Microsoft Foundry agent that retrieves from UN documents with file search."),
    ("Next", "Ingest more documents into the current collection, and add new collections such as space debris and space resources."),
    ("Later", "A vetted vector database on Azure AI Search for researchers (currently limited by my school Azure account)."),
]

st.set_page_config(
    page_title="Tether",
    page_icon="🛰️",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Theme: one stylesheet. Effects sit on the frame (header, background, borders);
# the conversation text itself stays plain and readable.
# ---------------------------------------------------------------------------
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@600;800&family=JetBrains+Mono:wght@400;600&family=Share+Tech+Mono&display=swap');

:root {
  --void: #06080B;
  --panel: #0C1117;
  --panel-2: #10161E;
  --text: #D8F5E8;
  --muted: #7C9A8E;
  --green: #00FF9C;
  --magenta: #FF2BD6;
  --cyan: #00E5FF;
  --error: #FF3B6B;
  --cut: 14px;
  --font-head: 'Orbitron', sans-serif;
  --font-body: 'JetBrains Mono', ui-monospace, monospace;
  --font-label: 'Share Tech Mono', ui-monospace, monospace;
}

/* ---------- Base ---------- */
.stApp {
  background-color: var(--void);
  background-image:
    linear-gradient(rgba(0,229,255,0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0,229,255,0.035) 1px, transparent 1px);
  background-size: 32px 32px;
  color: var(--text);
}
.stApp, .stMarkdown, p, li, input, textarea, button {
  font-family: var(--font-body) !important;
}
[data-testid="stHeader"] { background: transparent; }

/* CRT scanlines over everything; never blocks clicks */
.stApp::after {
  content: "";
  position: fixed; inset: 0;
  pointer-events: none;
  z-index: 999;
  background: repeating-linear-gradient(
    to bottom, rgba(0,0,0,0) 0px, rgba(0,0,0,0) 2px, rgba(0,0,0,0.18) 3px);
}

a { color: var(--cyan) !important; }
a:hover { color: var(--green) !important; }

/* ---------- Header ---------- */
.tt-header { margin: 0.5rem 0 1.6rem; }
.tt-title {
  position: relative;
  display: inline-block;
  font-family: var(--font-head);
  font-weight: 800;
  font-size: clamp(2.6rem, 10vw, 4.4rem);
  letter-spacing: 0.18em;
  color: var(--green);
  text-shadow: 0 0 6px rgba(0,255,156,0.6), 0 0 22px rgba(0,255,156,0.25);
  line-height: 1;
}
/* Colour-split copies, visible only during the brief glitch */
.tt-title::before, .tt-title::after {
  content: attr(data-text);
  position: absolute; left: 0; top: 0;
  opacity: 0;
}
.tt-title::before { color: var(--magenta); animation: glitch-a 7s infinite steps(1); }
.tt-title::after  { color: var(--cyan);    animation: glitch-b 7s infinite steps(1); }
@keyframes glitch-a {
  0%, 93%, 100% { opacity: 0; transform: none; clip-path: none; }
  94% { opacity: .85; transform: translate(-3px, 1px); clip-path: inset(10% 0 55% 0); }
  96% { opacity: .85; transform: translate(2px, -1px); clip-path: inset(60% 0 8% 0); }
}
@keyframes glitch-b {
  0%, 93%, 100% { opacity: 0; transform: none; clip-path: none; }
  95% { opacity: .85; transform: translate(3px, -1px); clip-path: inset(30% 0 35% 0); }
  97% { opacity: .85; transform: translate(-2px, 1px); clip-path: inset(0 0 70% 0); }
}
.tt-status {
  margin-top: 0.7rem;
  font-family: var(--font-label);
  font-size: 0.9rem;
  letter-spacing: 0.08em;
  color: var(--muted);
}
.tt-status .ok { color: var(--green); }
.tt-cursor {
  display: inline-block;
  width: 0.55em; height: 1em;
  margin-left: 0.25em;
  vertical-align: -0.15em;
  background: var(--green);
  animation: blink 1.1s steps(1) infinite;
}
@keyframes blink { 50% { opacity: 0; } }

/* ---------- Angular panels ----------
   clip-path cuts the corners; an inset shadow draws the glowing edge inside
   the cut so the glow isn't clipped away. */
.tt-panel, [data-testid="stChatMessage"] {
  clip-path: polygon(var(--cut) 0, 100% 0, 100% calc(100% - var(--cut)),
                     calc(100% - var(--cut)) 100%, 0 100%, 0 var(--cut));
}

/* Boot sequence (empty state) */
.tt-boot {
  background: var(--panel);
  box-shadow: inset 0 0 0 1px rgba(0,255,156,0.55);
  padding: 1rem 1.2rem;
  margin-bottom: 1rem;
  font-family: var(--font-label);
  font-size: 0.92rem;
  line-height: 1.7;
  color: var(--muted);
}
.tt-boot .ok { color: var(--green); }
.tt-boot .line { white-space: pre-wrap; }
.tt-purpose {
  font-family: var(--font-body);
  font-size: 0.85rem;
  line-height: 1.6;
  color: var(--text);
  max-width: 40em;
}
.tt-using {
  margin-top: 0.6rem;
  font-size: 0.78rem;
  line-height: 1.55;
  color: var(--muted);
  max-width: 44em;
}
.tt-using .k { color: var(--green); margin-right: 0.3rem; }

/* Suggested queries: quiet until hovered */
[class*="st-key-ex-"] button {
  height: 3rem !important;
  min-height: 3rem !important;
  background: transparent !important;
  border: 1px solid rgba(0,255,156,0.22) !important;
  color: var(--muted) !important;
  font-size: 0.8rem !important;
  opacity: 0.75;
}
[class*="st-key-ex-"] button p { font-size: 0.8rem; }
[class*="st-key-ex-"] button::before { color: rgba(255,43,214,0.6) !important; }
[class*="st-key-ex-"] button:hover,
[class*="st-key-ex-"] button:focus-visible {
  opacity: 1;
  color: var(--green) !important;
  border-color: rgba(0,255,156,0.6) !important;
  background: rgba(0,255,156,0.06) !important;
}

/* Clear conversation: a small text control, right-aligned above the chat */
.st-key-clear { display: flex; justify-content: flex-end; }
.st-key-clear button {
  width: auto !important;
  min-height: 0 !important;
  height: auto !important;
  padding: 0.2rem 0.4rem !important;
  background: transparent !important;
  border: none !important;
  clip-path: none !important;
  color: var(--muted) !important;
  font-size: 0.78rem !important;
  letter-spacing: 0.06em;
}
.st-key-clear button::before { content: "\\21BA  " !important; color: var(--cyan) !important; }
.st-key-clear button p { font-size: 0.78rem; }
.st-key-clear button:hover { color: var(--green) !important; background: transparent !important; }

.tt-label {
  font-family: var(--font-label);
  text-transform: uppercase;
  letter-spacing: 0.16em;
  font-size: 0.8rem;
  color: var(--cyan);
  margin: 0.4rem 0 0.6rem;
}

/* ---------- Messages ---------- */
[data-testid="stChatMessage"] {
  background: var(--panel);
  padding: 1rem 1.1rem;
  margin-bottom: 0.9rem;
  box-shadow: inset 0 0 0 1px rgba(0,255,156,0.5);
  border-left: 3px solid var(--green);
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  background: var(--panel-2);
  box-shadow: inset 0 0 0 1px rgba(255,43,214,0.5);
  border-left: 3px solid var(--magenta);
}
[data-testid="stChatMessage"] p,
[data-testid="stChatMessage"] li {
  color: var(--text);
  font-size: 0.95rem;
  line-height: 1.65;
}

/* Sources */
.tt-sources {
  margin-top: 0.9rem;
  padding-top: 0.7rem;
  border-top: 1px dashed rgba(0,229,255,0.35);
}
.tt-sources .tt-label { margin: 0 0 0.4rem; }
.tt-source {
  display: block;
  font-size: 0.85rem;
  line-height: 1.5;
  margin-bottom: 0.35rem;
  color: var(--text);
}
.tt-source .ref { font-family: var(--font-label); color: var(--muted); }

/* Errors */
.tt-error {
  background: rgba(255,59,107,0.08);
  box-shadow: inset 0 0 0 1px var(--error);
  border-left: 3px solid var(--error);
  padding: 0.8rem 1rem;
  color: #FFD6E0;
  font-size: 0.9rem;
}
.tt-error .code { font-family: var(--font-label); color: var(--error); letter-spacing: 0.08em; }

/* ---------- Buttons (prompts + sidebar) ---------- */
.stButton > button {
  width: 100%;
  min-height: 3rem;
  justify-content: flex-start;
  text-align: left;
  background: transparent;
  color: var(--green);
  border: 1px solid rgba(0,255,156,0.6);
  border-radius: 0;
  font-family: var(--font-label) !important;
  letter-spacing: 0.04em;
  clip-path: polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%, 0 10px);
  transition: background 120ms linear, color 120ms linear;
}
.stButton > button::before { content: "> "; color: var(--magenta); white-space: pre; }

/* Suggested queries (the buttons inside columns): identical boxes,
   whatever the label length */
[data-testid="stHorizontalBlock"] .stButton,
[data-testid="stHorizontalBlock"] .stButton > button {
  width: 100%;
}
[data-testid="stHorizontalBlock"] .stButton > button {
  height: 3.4rem;
  min-height: 3.4rem;
  align-items: center;
  overflow: hidden;
}
[data-testid="stHorizontalBlock"] .stButton > button p {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin: 0;
}
.stButton > button:hover {
  background: rgba(0,255,156,0.12);
  color: #FFFFFF;
  border-color: var(--green);
}
.stButton > button:focus-visible {
  outline: 2px solid var(--cyan);
  outline-offset: 2px;
}

/* ---------- Command-prompt input ---------- */
[data-testid="stChatInput"] {
  background: var(--panel) !important;
  border: 1px solid rgba(0,255,156,0.45) !important;
  border-radius: 0 !important;
  transition: border-color 120ms linear, box-shadow 120ms linear;
}
[data-testid="stChatInput"]:focus-within {
  border-color: var(--green) !important;
  box-shadow: 0 0 0 1px var(--green), 0 0 18px rgba(0,255,156,0.35);
}
/* Inner wrappers: square, same fill as the box, no inner border,
   so the colour runs right up to the green edge */
[data-testid="stChatInput"] > div,
[data-testid="stChatInput"] [data-baseweb="textarea"],
[data-testid="stChatInput"] [data-baseweb="base-input"],
[data-testid="stChatInput"] textarea {
  background: var(--panel) !important;
  border-radius: 0 !important;
  border: none !important;
}
[data-testid="stChatInput"] textarea {
  color: var(--text) !important;
  caret-color: var(--green);
}
[data-testid="stChatInput"] button { border-radius: 0 !important; }
[data-testid="stChatInput"] textarea::placeholder { color: var(--muted); }
[data-testid="stBottom"] > div { background: var(--void); }

/* ---------- Sidebar control panel ---------- */
[data-testid="stSidebar"] {
  background: #080B0F;
  border-right: 1px solid rgba(0,229,255,0.3);
}
@media (min-width: 768px) {
  [data-testid="stSidebar"] { min-width: 26rem; max-width: 26rem; }
}
[data-testid="stSidebar"] .tt-label { margin-top: 1.2rem; }

/* Sidebar heading: the "Collection" title outranks everything under it */
.tt-side-title {
  font-family: var(--font-head);
  font-weight: 600;
  font-size: 1rem;
  letter-spacing: 0.12em;
  color: var(--text);
  margin: 0.4rem 0 0.35rem;
}
.tt-sidenote { font-size: 0.72rem; line-height: 1.5; color: var(--muted); margin: 0 0 0.8rem; }
.tt-side-title.tt-gap { margin-top: 1.4rem; }

/* ---------- Catalog ---------- */
.tt-stats {
  display: flex; flex-wrap: wrap; gap: 1.2rem;
  margin-top: 0.8rem;
  font-family: var(--font-label);
  font-size: 0.8rem;
  color: var(--muted);
}
.tt-stats b { color: var(--text); font-size: 1rem; margin-right: 0.25rem; }
.tt-stats .ok b { color: var(--green); }
.tt-count {
  font-family: var(--font-label);
  font-size: 0.75rem;
  color: var(--muted);
  margin: 0.2rem 0 0.6rem;
}
.tt-doc {
  background: var(--panel);
  box-shadow: inset 0 0 0 1px rgba(124,154,142,0.2);
  border-left: 2px solid var(--green);
  padding: 0.7rem 0.9rem;
  margin-bottom: 0.5rem;
}
.tt-doc .title { font-size: 0.88rem; line-height: 1.45; color: var(--text); }
.tt-doc .meta {
  margin-top: 0.3rem;
  font-family: var(--font-label);
  font-size: 0.72rem;
  color: var(--muted);
  word-break: break-word;
}
/* Filters: square, dark fields with a quiet border */
[data-testid="stSelectbox"] label p, [data-testid="stTextInput"] label p {
  font-family: var(--font-label) !important;
  font-size: 0.75rem;
  color: var(--muted);
}
[data-testid="stSelectbox"] [data-baseweb="select"] > div,
[data-testid="stTextInput"] [data-baseweb="input"] {
  border-radius: 0 !important;
  background: var(--panel) !important;
  border-color: rgba(124,154,142,0.3) !important;
}

/* Collection rows (buttons): small, quiet, no prefix */
[class*="st-key-col-"] button,
[class*="st-key-colsel-"] button {
  min-height: 0 !important;
  height: auto !important;
  padding: 0.4rem 0.7rem !important;
  background: transparent !important;
  border: 1px solid rgba(124,154,142,0.2) !important;
  border-left: 2px solid transparent !important;
  clip-path: none !important;
  color: var(--muted) !important;
}
[class*="st-key-col-"] button::before,
[class*="st-key-colsel-"] button::before { content: none !important; }
[class*="st-key-col-"] button p,
[class*="st-key-colsel-"] button p { font-size: 0.8rem; }
[class*="st-key-col-"] button:hover { color: var(--text) !important; }
[class*="st-key-colsel-"] button {
  color: var(--text) !important;
  border-color: rgba(0,229,255,0.3) !important;
  border-left-color: var(--cyan) !important;
  background: rgba(0,229,255,0.04) !important;
  cursor: default;
}
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:focus-visible) {
  outline: 2px solid var(--cyan);
  outline-offset: 2px;
}

/* "System details" toggle */
[data-testid="stSidebar"] [data-testid="stExpander"] {
  border: 1px solid rgba(0,229,255,0.3);
  border-radius: 0;
  background: transparent;
  margin-top: 1.2rem;
}
[data-testid="stSidebar"] [data-testid="stExpander"] details,
[data-testid="stSidebar"] [data-testid="stExpander"] summary { border-radius: 0; }
[data-testid="stSidebar"] [data-testid="stExpander"] summary p {
  font-family: var(--font-label) !important;
  color: var(--cyan);
  letter-spacing: 0.08em;
}

/* Roadmap strip under the title */
.tt-roadmap {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.6rem;
  margin-top: 1rem;
}
.tt-roadmap .step {
  border-top: 2px solid rgba(0,229,255,0.4);
  padding-top: 0.45rem;
  font-size: 0.8rem;
  line-height: 1.45;
  color: var(--muted);
}
.tt-roadmap .step.now { border-top-color: var(--green); color: var(--text); }
.tt-roadmap .k {
  display: block;
  font-family: var(--font-label);
  font-size: 0.72rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--cyan);
  margin-bottom: 0.15rem;
}
.tt-roadmap .step.now .k { color: var(--green); }
@media (max-width: 640px) {
  .tt-roadmap { grid-template-columns: 1fr; }
}
.tt-planned {
  font-family: var(--font-label);
  font-size: 0.75rem;
  color: var(--muted);
  opacity: 0.7;
  margin: 0.3rem 0 0 0.75rem;
}
.tt-planned .tag { color: var(--magenta); margin-left: 0.4rem; }
.tt-spec { font-family: var(--font-label); font-size: 0.85rem; margin-bottom: 0.7rem; }
.tt-spec .k { color: var(--muted); display: block; letter-spacing: 0.1em; text-transform: uppercase; font-size: 0.75rem; }
.tt-spec .v { color: var(--green); }
.tt-note { color: var(--muted); font-size: 0.68rem; line-height: 1.5; opacity: 0.8; margin: 0; }

/* Pin the disclaimer to the bottom of the sidebar: the sidebar's column
   fills the screen height and the disclaimer takes up the leftover space above it */
[data-testid="stSidebarUserContent"] [data-testid="stVerticalBlock"]:has(> .st-key-disclaimer) {
  min-height: calc(100vh - 7rem);
}
.st-key-disclaimer { margin-top: auto; padding-top: 1rem; }

/* Spinner text */
[data-testid="stSpinner"] p { font-family: var(--font-label) !important; color: var(--cyan); }

/* ---------- Accessibility ---------- */
@media (prefers-reduced-motion: reduce) {
  .tt-title::before, .tt-title::after, .tt-cursor { animation: none !important; }
  .stButton > button, [data-testid="stChatInput"] { transition: none; }
}
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []  # list of {role, content, citations}
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None
if "session_id" not in st.session_state:
    # Identifies this browser session to the backend's per-visitor rate limit.
    st.session_state.session_id = uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Render helpers (all text going into custom HTML is escaped)
# ---------------------------------------------------------------------------
def esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def render_sources(citations: list[dict]) -> None:
    if not citations:
        return
    rows = []
    for c in citations:
        title = c.get("title") or c.get("filename") or c.get("file_id") or "Unknown document"
        ref = " // ".join(esc(x) for x in (c.get("symbol"), c.get("year")) if x)
        ref_html = f' <span class="ref">[{ref}]</span>' if ref else ""
        if c.get("url"):
            body = f'<a href="{esc(c["url"])}" target="_blank" rel="noopener noreferrer">{esc(title)}</a>'
        else:
            body = esc(title)
        rows.append(f'<span class="tt-source">{body}{ref_html}</span>')
    st.markdown(
        '<div class="tt-sources"><div class="tt-label">Sources</div>' + "".join(rows) + "</div>",
        unsafe_allow_html=True,
    )


def render_error(message: str) -> None:
    st.markdown(
        f'<div class="tt-error tt-panel"><span class="code">ERR //</span> {esc(message)}</div>',
        unsafe_allow_html=True,
    )


def typewriter(text: str):
    """Type the finished answer out word by word (the backend doesn't stream yet)."""
    for word in text.split(" "):
        yield word + " "
        time.sleep(0.012)


@st.cache_resource(show_spinner=False)
def embedded_agents():
    """One shared pool of agent clients for the whole Streamlit server."""
    from backend import service

    return service.AgentPool()


def ask_backend(question: str, collection_id: str) -> dict:
    if not BACKEND_URL:
        from backend import service

        try:
            return service.chat(
                embedded_agents(),
                question,
                collection_id,
                st.session_state.conversation_id,
                visitor=st.session_state.session_id,
            )
        except service.ServiceError as exc:
            raise RuntimeError(exc.detail) from None

    resp = requests.post(
        f"{BACKEND_URL}/chat",
        json={
            "message": question,
            "collection": collection_id,
            "conversation_id": st.session_state.conversation_id,
        },
        headers={"X-Session-Id": st.session_state.session_id},
        timeout=120,
    )
    if resp.status_code in (400, 404, 429, 501):
        raise RuntimeError(resp.json().get("detail", "Request rejected. Try again."))
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_collections() -> list[dict]:
    """The collection registry, refreshed every 5 minutes."""
    if not BACKEND_URL:
        from backend import service

        return service.public_collections()
    resp = requests.get(f"{BACKEND_URL}/collections", timeout=10)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_catalog(collection_id: str) -> dict:
    """Every document a collection answers from."""
    if not BACKEND_URL:
        from backend import service

        return service.catalog(collection_id)
    resp = requests.get(f"{BACKEND_URL}/collections/{collection_id}/documents", timeout=10)
    resp.raise_for_status()
    return resp.json()


def render_catalog(collection: dict) -> None:
    try:
        cat = fetch_catalog(collection["id"])
    except Exception:
        render_error("Couldn't load the document catalog for this collection.")
        return

    st.markdown(
        f"""
<div class="tt-boot tt-panel">
  <div class="tt-purpose">The vetted documents Tether answers from for {esc(collection["name"]).lower()}.
  Each one links to its official source.</div>
  <div class="tt-stats"><span class="ok"><b>{cat["total"]}</b> documents</span></div>
</div>
""",
        unsafe_allow_html=True,
    )

    docs = cat["documents"]
    if not docs:
        st.markdown('<p class="tt-note">No documents catalogued yet.</p>', unsafe_allow_html=True)
        return

    c1, c2 = st.columns([2, 3])
    with c1:
        types = sorted({d["type"] for d in docs if d.get("type")})
        doc_type = st.selectbox("Type", ["All types"] + types, key="cat_type")
    with c2:
        query = st.text_input("Search", placeholder="Title, symbol or country", key="cat_query")

    q = query.strip().lower()
    shown = [
        d for d in docs
        if (doc_type == "All types" or d.get("type") == doc_type)
        and (not q or q in " ".join(str(d.get(k) or "") for k in ("title", "symbol", "submitted_by")).lower())
    ]

    st.markdown(
        f'<div class="tt-count">Showing {len(shown)} of {len(docs)}</div>',
        unsafe_allow_html=True,
    )
    rows = []
    for d in shown:
        meta = [d.get("symbol"), d.get("year"), d.get("type"), d.get("submitted_by")]
        meta_html = " // ".join(esc(m) for m in meta if m)
        title = esc(d["title"])
        if d.get("url"):
            title = f'<a href="{esc(d["url"])}" target="_blank" rel="noopener noreferrer">{title}</a>'
        rows.append(
            f'<div class="tt-doc"><div class="title">{title}</div>'
            f'<div class="meta">{meta_html}</div></div>'
        )
    st.markdown("".join(rows) or '<p class="tt-note">No documents match these filters.</p>', unsafe_allow_html=True)


def reset_conversation() -> None:
    # Each collection has its own agent, so a conversation can't carry across.
    st.session_state.messages = []
    st.session_state.conversation_id = None


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------
try:
    collections = fetch_collections()
except Exception:
    if BACKEND_URL:
        render_error(f"Can't reach the Tether API at {BACKEND_URL}. Start the backend, then refresh this page.")
    else:
        render_error("Couldn't load the collections registry (backend/collections.json).")
    st.stop()

live = [c for c in collections if c["status"] == "live"]
planned = [c for c in collections if c["status"] != "live"]
if not live:
    render_error("No collections are live yet.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar: collection picker, system panel, roadmap
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        '<div class="tt-side-title">Collection</div>'
        '<p class="tt-sidenote">Different subjects to study, each answered from its own set of vetted documents.</p>',
        unsafe_allow_html=True,
    )
    # Plain rows instead of a radio group: no dot or icon to fight with.
    # The selected row's key starts with "colsel-" so CSS can mark it.
    if st.session_state.get("collection_id") not in {c["id"] for c in live}:
        st.session_state.collection_id = live[0]["id"]
    for c in live:
        is_selected = c["id"] == st.session_state.collection_id
        key = f"colsel-{c['id']}" if is_selected else f"col-{c['id']}"
        if st.button(c["name"], key=key) and not is_selected:
            st.session_state.collection_id = c["id"]
            reset_conversation()
            st.rerun()
    selected_id = st.session_state.collection_id
    for c in planned:
        st.markdown(
            f'<div class="tt-planned">{esc(c["name"])}<span class="tag">planned</span></div>',
            unsafe_allow_html=True,
        )

    collection = next(c for c in live if c["id"] == selected_id)

    st.markdown('<div class="tt-side-title tt-gap">Services</div>', unsafe_allow_html=True)
    if "view" not in st.session_state:
        st.session_state.view = "chat"
    for view_id, label in (("chat", "Ask questions"), ("catalog", "Browse documents")):
        is_selected = st.session_state.view == view_id
        key = f"colsel-view-{view_id}" if is_selected else f"col-view-{view_id}"
        if st.button(label, key=key) and not is_selected:
            st.session_state.view = view_id
            st.rerun()

    with st.expander("System details", expanded=False):
        specs = [
            ("Model", MODEL),
            ("Platform", PLATFORM),
            ("Retrieval", collection["retrieval"]),
            ("Corpus", collection["description"]),
            ("Rate limit", RATE_LIMIT),
        ]
        st.markdown(
            "".join(
                f'<div class="tt-spec"><span class="k">{esc(k)}</span><span class="v">{esc(v)}</span></div>'
                for k, v in specs
            ),
            unsafe_allow_html=True,
        )

    with st.container(key="disclaimer"):
        st.markdown(
            '<p class="tt-note">Answers come only from official UN documents. '
            "The guidelines are voluntary; this is not legal advice.</p>",
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Header: title, status line, roadmap
# ---------------------------------------------------------------------------
roadmap_html = "".join(
    f'<div class="step{" now" if i == 0 else ""}"><span class="k">{esc(stage)}</span>{esc(text)}</div>'
    for i, (stage, text) in enumerate(ROADMAP)
)
st.markdown(
    f"""
<div class="tt-header">
  <div class="tt-title" data-text="TETHER">TETHER</div>
  <div class="tt-roadmap">{roadmap_html}</div>
</div>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Catalog view
# ---------------------------------------------------------------------------
if st.session_state.view == "catalog":
    render_catalog(collection)
    st.stop()

# ---------------------------------------------------------------------------
# Empty state: boot sequence + suggested prompts
# ---------------------------------------------------------------------------
if not st.session_state.messages:
    st.markdown(
        f"""
<div class="tt-boot tt-panel">
  <div class="tt-purpose">An MVP that tethers questions and answers to actual authoritative work.
  Every answer links back to its source.</div>
  <div class="tt-using"><span class="k">Currently using</span> {esc(collection["description"])}</div>
</div>
""",
        unsafe_allow_html=True,
    )
    # Suggested queries sit just above the input, pinned to the bottom like
    # other chat apps. st.bottom is the container Streamlit pins chat_input
    # into (st._bottom on older versions); otherwise use the normal flow.
    examples = collection.get("examples") or []
    if examples:
        bottom = getattr(st, "bottom", None) or getattr(st, "_bottom", None) or st.container()
        with bottom:
            cols = st.columns(2)
            for i, (label, question) in enumerate(examples):
                with cols[i % 2]:
                    if st.button(label, key=f"ex-{collection['id']}-{i}", help=question):
                        st.session_state.pending = question
                        st.rerun()

# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
if st.session_state.messages:
    if st.button("Clear conversation", key="clear"):
        reset_conversation()
        st.rerun()

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        render_sources(msg.get("citations", []))

question = st.chat_input("> enter query")
question = question or st.session_state.pop("pending", None)

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):
        with st.spinner("establishing uplink..."):
            try:
                data = ask_backend(question, collection["id"])
            except Exception as exc:
                render_error(str(exc))
                st.session_state.messages.pop()  # drop the unanswered question
                st.stop()

        st.write_stream(typewriter(data["answer"]))
        render_sources(data["citations"])

    st.session_state.conversation_id = data["conversation_id"]
    st.session_state.messages.append(
        {"role": "assistant", "content": data["answer"], "citations": data["citations"]}
    )
    st.rerun()  # hide the boot screen once the chat has begun