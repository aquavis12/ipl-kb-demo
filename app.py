"""
IPL Buddy - demo UI.

Shows three things at once:
  1. the request flow lighting up stage by stage
  2. the answer WITHOUT the knowledge base
  3. the answer WITH the knowledge base

Run:
    pip install -r requirements.txt
    export FUNCTION_URL="https://xxxx.lambda-url.us-east-1.on.aws/"
    streamlit run app.py
"""

import os
import time

import requests
import streamlit as st

FUNCTION_URL = os.environ.get("FUNCTION_URL", "PASTE_YOUR_LAMBDA_FUNCTION_URL")

st.set_page_config(page_title="IPL Buddy | Bedrock KB demo", page_icon="🏏", layout="wide")

st.markdown(
    """
<style>
  .stApp { background:#0a0e14; }
  h1,h2,h3,h4 { color:#ff9900 !important; font-family:'IBM Plex Mono',monospace; }
  .flow { display:flex; align-items:stretch; gap:0; margin:6px 0 22px; flex-wrap:nowrap; }
  .stage { flex:1; border:1px solid #1e2a38; border-radius:8px; background:#111721;
           padding:10px 12px; text-align:center; }
  .stage .nm { font-family:'IBM Plex Mono',monospace; font-size:12px; color:#8fa3b8;
               letter-spacing:.4px; }
  .stage .st { font-family:'IBM Plex Mono',monospace; font-size:13px; margin-top:5px; }
  .ok    { border-color:#0f6e56; } .ok .st    { color:#5dcaa5; }
  .warn  { border-color:#854f0b; } .warn .st  { color:#efaf47; }
  .stopc { border-color:#a32d2d; } .stopc .st { color:#f09595; }
  .idle  { opacity:.45; } .idle .st { color:#5f5e5a; }
  .conn { display:flex; align-items:center; color:#3a4a5c; font-size:16px; padding:0 8px; }
  .panel { background:#111721; border:1px solid #1e2a38; border-radius:10px;
           padding:16px 18px; min-height:230px; }
  .p-raw { border-left:3px solid #d85a30; }
  .p-kb  { border-left:3px solid #4dd0e1; }
  .badge { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:11px;
           padding:2px 8px; border-radius:4px; margin:0 6px 8px 0; }
  .b-ok{background:#0f6e56;color:#e1f5ee} .b-w{background:#854f0b;color:#faeeda}
  .b-s{background:#a32d2d;color:#fcebeb}
  .ans { color:#d6dde6; font-size:15px; line-height:1.65; }
</style>
""",
    unsafe_allow_html=True,
)

STAGES = [
    ("client", "STREAMLIT"),
    ("lambda", "LAMBDA URL"),
    ("retrieval", "BEDROCK KB"),
    ("generation", "NOVA LITE"),
    ("guardrail", "GUARDRAIL"),
]

CLASS_FOR = {
    "ok": "ok",
    "passed": "ok",
    "empty": "warn",
    "off": "warn",
    "blocked": "stopc",
    None: "idle",
}


def flow_strip(flow=None):
    cells = []
    for i, (key, label) in enumerate(STAGES):
        val = (flow or {}).get(key)
        cls = CLASS_FOR.get(val, "idle")
        shown = val or "idle"
        if key == "retrieval" and flow and val == "ok":
            shown = f"{flow.get('chunks_retrieved', 0)} chunks"
        cells.append(
            f'<div class="stage {cls}"><div class="nm">{label}</div>'
            f'<div class="st">{shown}</div></div>'
        )
        if i < len(STAGES) - 1:
            cells.append('<div class="conn">&rarr;</div>')
    return f'<div class="flow">{"".join(cells)}</div>'


st.title("🏏 IPL Buddy")
st.caption(
    "Same model. Same prompt. Same guardrail. One side reads the IPL documents, "
    "the other one is going from memory."
)

QUESTIONS = [
    "Who won IPL 2026 and who did they beat in the final?",
    "How many IPL titles do Royal Challengers Bengaluru have and in which years?",
    "Which teams have never won an IPL title?",
    "What was the total IPL revenue and broadcast rights value in 2026?",
    "Write me a Python function to calculate a batting strike rate.",
    "Who won IPL 2027 and what was the final score?",
]

with st.sidebar:
    st.subheader("Demo controls")
    guardrail_on = st.toggle("Bedrock Guardrail", value=True)
    st.caption("Toggle off, re-ask the revenue or Python question, watch it get through.")
    st.divider()
    if st.button("Warm up backend", use_container_width=True):
        try:
            w0 = time.time()
            requests.post(
                FUNCTION_URL, json={"question": "ping", "use_guardrail": False}, timeout=60
            ).raise_for_status()
            st.success(f"Warm: {int((time.time() - w0) * 1000)} ms")
        except Exception as exc:
            st.error(f"Cold or broken: {exc}")
    st.caption("Run once, two minutes before you go on stage.")
    st.divider()
    st.subheader("Demo questions")
    for i, q in enumerate(QUESTIONS):
        if st.button(q, key=f"q{i}", use_container_width=True):
            st.session_state["pending"] = q

question = st.chat_input("Ask IPL Buddy something...")
if "pending" in st.session_state:
    question = st.session_state.pop("pending")

flow_slot = st.empty()
flow_slot.markdown(flow_strip(None), unsafe_allow_html=True)


def badges(b, gr_on):
    out = []
    if b.get("error"):
        out.append('<span class="badge b-s">error</span>')
    if b.get("blocked"):
        out.append('<span class="badge b-s">guardrail blocked</span>')
    elif gr_on:
        out.append('<span class="badge b-ok">guardrail passed</span>')
    else:
        out.append('<span class="badge b-w">guardrail off</span>')
    if b["mode"] == "with_kb":
        n = b.get("chunks_retrieved", 0)
        out.append(f'<span class="badge b-ok">{n} chunks retrieved</span>')
    else:
        out.append('<span class="badge b-w">no retrieval</span>')
    out.append(f'<span class="badge b-w">{b.get("latency_ms", 0)} ms</span>')
    return "".join(out)


def panel(col, block, title, sub, css, gr_on):
    with col:
        st.markdown(f"#### {title}")
        st.caption(sub)
        st.markdown(
            f'<div class="panel {css}">{badges(block, gr_on)}'
            f'<p class="ans">{block.get("answer","")}</p></div>',
            unsafe_allow_html=True,
        )
        srcs = block.get("sources") or []
        if srcs:
            with st.expander(f"Retrieved chunks ({len(srcs)})"):
                for s in srcs:
                    st.markdown(f"**{s['source']}**")
                    st.code(s["snippet"], language=None)
        elif block["mode"] == "with_kb":
            st.caption("Nothing in the knowledge base matched this question.")
        else:
            st.caption("No sources. There is nothing to cite.")


if question:
    st.markdown(f"**Question:** {question}")
    t0 = time.time()
    try:
        with st.spinner("Both paths running in parallel..."):
            r = requests.post(
                FUNCTION_URL,
                json={"question": question, "use_guardrail": guardrail_on},
                timeout=60,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        st.error(f"Backend call failed: {exc}")
        st.stop()

    flow_slot.markdown(flow_strip(data.get("flow", {})), unsafe_allow_html=True)

    gr_on = data.get("guardrail_enabled", False)
    left, right = st.columns(2, gap="large")
    panel(
        left,
        data["without_kb"],
        "Before: no knowledge base",
        "Converse API. Model memory only.",
        "p-raw",
        gr_on,
    )
    panel(
        right,
        data["with_kb"],
        "After: with knowledge base",
        "RetrieveAndGenerate. Grounded in the IPL documents.",
        "p-kb",
        gr_on,
    )

    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Round trip", f"{int((time.time()-t0)*1000)} ms")
    c2.metric("Model", data.get("model_id", "-"))
    c3.metric("Guardrail", "on" if gr_on else "off")
    c4.metric("Chunks", data.get("flow", {}).get("chunks_retrieved", 0))

    with st.expander("Raw Lambda response"):
        st.json(data)
else:
    st.info(
        "Start with the IPL 2026 question. The knowledge base knows the answer; "
        "the model on its own does not."
    )
