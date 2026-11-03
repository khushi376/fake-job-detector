import base64
import json
import requests
import streamlit as st

API = "http://localhost:8000"

st.set_page_config(page_title="Fake Job Posting Detector", page_icon="🛡️", layout="centered")

DEFAULTS = {"token": None, "email": None, "role": None, "result": None, "text": "", "fb": None, "msg": None}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

def role_from_token(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role", "user")
    except Exception:
        return "user"

def auth_headers():
    return {"Authorization": f"Bearer {st.session_state.token}"}

def logout(message=None):
    for k in ("token", "email", "role", "result", "fb"):
        st.session_state[k] = None
    st.session_state.text = ""
    st.session_state.msg = message

def api_error(r):
    try:
        d = r.json().get("detail", r.text)
    except Exception:
        d = r.text
    if isinstance(d, list):
        d = "; ".join(str(x.get("msg", x)) for x in d)
    return str(d)

def do_login(email, password):
    try:
        r = requests.post(f"{API}/auth/login", data={"username": email, "password": password}, timeout=30)
    except requests.exceptions.RequestException:
        return "Cannot reach the backend. Is uvicorn running?"
    if r.status_code == 200:
        tok = r.json()["access_token"]
        st.session_state.token = tok
        st.session_state.email = email.strip().lower()
        st.session_state.role = role_from_token(tok)
        st.session_state.msg = None
        return None
    return api_error(r)

def do_register(email, password):
    try:
        r = requests.post(f"{API}/auth/register", json={"email": email, "password": password}, timeout=30)
    except requests.exceptions.RequestException:
        return "Cannot reach the backend. Is uvicorn running?"
    if r.status_code in (200, 201):
        return None
    return api_error(r)

def send_feedback(label, score):
    try:
        r = requests.post(f"{API}/feedback", json={"posting_text": st.session_state.text, "predicted_score": score, "user_label": label}, headers=auth_headers(), timeout=30)
    except requests.exceptions.RequestException:
        st.session_state.fb = "Could not reach the backend."
        return
    if r.status_code in (200, 201):
        st.session_state.fb = "Thanks! Your feedback was saved and will be reviewed by a moderator."
    elif r.status_code == 401:
        logout("Your session expired. Please log in again.")
    else:
        st.session_state.fb = f"Could not save feedback: {api_error(r)}"

def render_feed():
    st.subheader("Public feed of flagged postings")
    q = st.text_input("Search flagged postings", key="feed_q", placeholder="e.g. whatsapp, registration fee")
    limit = st.slider("How many to show", 5, 50, 15, key="feed_limit")
    try:
        r = requests.get(f"{API}/postings", params={"q": q, "limit": limit, "offset": 0}, timeout=30)
        items = r.json() if r.status_code == 200 else []
    except requests.exceptions.RequestException:
        st.write("Backend not reachable.")
        return
    if isinstance(items, dict):
        items = items.get("items", items.get("postings", []))
    if not items:
        st.write("No flagged postings found.")
    for p in items:
        label = f"{p.get('verdict', '')} | {float(p.get('risk_score', 0)):.0f} | {str(p.get('text', ''))[:70]}..."
        with st.expander(label):
            st.write(str(p.get("text", ""))[:800])
            for reason in p.get("reasons", []):
                st.write(f"- {reason}")

def render_check():
    st.caption("Paste a job posting to get a scam-risk score and an explanation.")
    text = st.text_area("Job posting text", height=220, placeholder="Paste the job posting here...")
    if st.button("Check posting", type="primary"):
        if not text.strip():
            st.warning("Please paste a job posting first.")
        else:
            try:
                r = requests.post(f"{API}/predict", json={"text": text}, headers=auth_headers(), timeout=60)
                if r.status_code == 200:
                    st.session_state.result = r.json()
                    st.session_state.text = text
                    st.session_state.fb = None
                elif r.status_code == 401:
                    logout("Your session expired. Please log in again.")
                    st.rerun()
                else:
                    st.error(f"Backend error {r.status_code}: {api_error(r)}")
            except requests.exceptions.RequestException:
                st.error("Cannot reach the backend. Is uvicorn running?")
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

st.title("🛡️ Fake Job Posting Detector")

with st.sidebar:
    if st.session_state.token:
        st.write(f"Signed in as **{st.session_state.email}**")
        st.write(f"Role: {st.session_state.role}")
        if st.button("Log out"):
            logout()
            st.rerun()
    else:
        st.write("You are not signed in.")

if st.session_state.msg:
    st.warning(st.session_state.msg)

if st.session_state.token:
    tab_check, tab_feed = st.tabs(["Check a posting", "Public feed"])
    with tab_check:
        render_check()
    with tab_feed:
        render_feed()
else:
    tab_login, tab_register, tab_feed = st.tabs(["Log in", "Register", "Public feed"])
    with tab_login:
        with st.form("login_form"):
            le = st.text_input("Email")
            lp = st.text_input("Password", type="password")
            go = st.form_submit_button("Log in")
        if go:
            err = do_login(le, lp)
            if err:
                st.error(err)
            else:
                st.rerun()
    with tab_register:
        with st.form("register_form"):
            re_ = st.text_input("Email", key="reg_email")
            rp = st.text_input("Password (min 8 characters)", type="password", key="reg_pw")
            go2 = st.form_submit_button("Create account")
        if go2:
            err = do_register(re_, rp)
            if err:
                st.error(err)
            else:
                err2 = do_login(re_, rp)
                if err2:
                    st.success("Account created. Please log in.")
                else:
                    st.rerun()
    with tab_feed:
        render_feed()
