import requests
import streamlit as st

API = "http://localhost:8000"

st.set_page_config(page_title="Fake Job Posting Detector", page_icon="🛡️", layout="centered")
st.title("🛡️ Fake Job Posting Detector")
st.caption("Paste a job posting to get a scam-risk score and an explanation.")

if "result" not in st.session_state:
    st.session_state.result = None
    st.session_state.text = ""
    st.session_state.fb = None

text = st.text_area("Job posting text", height=220, placeholder="Paste the job posting here...")

if st.button("Check posting", type="primary"):
    if not text.strip():
        st.warning("Please paste a job posting first.")
    else:
        try:
            r = requests.post(f"{API}/predict", json={"text": text}, timeout=60)
            if r.status_code == 200:
                st.session_state.result = r.json()
                st.session_state.text = text
                st.session_state.fb = None
            else:
                st.error(f"Backend error {r.status_code}: {r.text}")
        except requests.exceptions.RequestException as e:
            st.error(f"Cannot reach the backend at {API}. Is uvicorn running? ({e})")

def send_feedback(label, score):
    try:
        r = requests.post(f"{API}/feedback", json={"posting_text": st.session_state.text, "predicted_score": score, "user_label": label}, timeout=30)
        if r.status_code in (200, 201):
            st.session_state.fb = "Thanks! Your feedback was saved."
        else:
            st.session_state.fb = f"Could not save feedback ({r.status_code})."
    except requests.exceptions.RequestException:
        st.session_state.fb = "Could not reach the backend."

res = st.session_state.result
if res:
    score = float(res["risk_score"])
    verdict = res["verdict"]
    icon = {"Low": "🟢", "Medium": "🟠", "High": "🔴"}.get(verdict, "⚪")
    st.subheader(f"{icon} {verdict} risk: {score:.0f}/100")
    st.progress(min(max(int(score), 0), 100))
    st.markdown("**Why this was flagged:**")
    for reason in res["reasons"]:
        st.write(f"- {reason}")
    st.markdown("**Help improve the model. What is this posting really?**")
    c1, c2 = st.columns(2)
    if c1.button("🚩 It is a scam"):
        send_feedback("scam", score)
    if c2.button("✅ It is genuine"):
        send_feedback("genuine", score)
    if st.session_state.fb:
        st.info(st.session_state.fb)

st.divider()
st.subheader("Recently flagged postings")
try:
    r = requests.get(f"{API}/postings", params={"limit": 10}, timeout=30)
    items = r.json() if r.status_code == 200 else []
    if isinstance(items, dict):
        items = items.get("items", items.get("postings", []))
    if not items:
        st.write("No flagged postings yet.")
    for p in items:
        label = f"{p.get('verdict', '')} | {float(p.get('risk_score', 0)):.0f} | {str(p.get('text', ''))[:70]}..."
        with st.expander(label):
            st.write(str(p.get("text", ""))[:600])
            for reason in p.get("reasons", []):
                st.write(f"- {reason}")
except requests.exceptions.RequestException:
    st.write("Backend not reachable.")
