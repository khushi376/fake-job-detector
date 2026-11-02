
import re
import numpy as np
from scipy.sparse import hstack, csr_matrix
import xgboost as xgb

URGENCY = ["urgent", "immediate", "immediately", "limited seats", "apply now", "hurry", "last date", "act fast", "today only", "asap"]
PAYMENT = ["registration fee", "security deposit", "processing fee", "training fee", "pay ", "payment", "refundable", "send money", "advance fee", "kit charges"]
PERSONAL = ["aadhaar", "aadhar", "pan card", "bank details", "account number", "otp", "passport copy", "ssn", "credit card", "password"]
FREE_CONTACT = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "whatsapp", "telegram", "wa.me"]
WFH = ["work from home", "work at home", "home based", "part time", "typing job", "data entry"]
NO_EXP = ["no experience", "no qualification", "freshers can apply", "no skills required", "anyone can apply"]
SALARY_RE = re.compile(r"(\$|₹|rs\.?|inr|usd|salary|per month|per hour|per day|lpa|ctc|lakh)")
COMPANY_RE = re.compile(r"(about us|about the company|who we are|our company|we are a )")

ENG_COLS = ["urgency_count", "payment_request", "personal_info_request", "free_contact", "work_from_home", "no_experience", "mentions_salary", "mentions_company", "caps_ratio", "exclamation_count", "log_words"]

ENG_SENTENCES = {
"urgency_count": "Uses urgency language (e.g. 'urgent', 'apply now')",
"payment_request": "Mentions a fee or payment from the applicant",
"personal_info_request": "Asks for sensitive personal or bank information",
"free_contact": "Uses a free email or WhatsApp/Telegram instead of a company contact",
"work_from_home": "Promotes easy work-from-home / data-entry style work",
"no_experience": "Claims no experience or qualification is needed",
"mentions_salary": "Salary/pay is mentioned in a way common in scam posts",
"mentions_company": "Describes the company in a way typical of this posting type",
"caps_ratio": "Unusually high share of CAPITAL LETTERS",
"exclamation_count": "Excessive exclamation marks",
"log_words": "Posting length is unusual for this kind of job",
}

def _count(text, words):
    return sum(text.count(w) for w in words)

def engineered_row(raw):
    raw = str(raw)
    t = raw.lower()
    letters = [c for c in raw if c.isalpha()]
    caps = sum(c.isupper() for c in letters) / max(len(letters), 1)
    return [_count(t, URGENCY), int(_count(t, PAYMENT) > 0), int(_count(t, PERSONAL) > 0), int(_count(t, FREE_CONTACT) > 0), int(_count(t, WFH) > 0), int(_count(t, NO_EXP) > 0), int(bool(SALARY_RE.search(t))), int(bool(COMPANY_RE.search(t))), caps, raw.count("!"), np.log1p(len(raw.split()))]

def engineered_matrix(texts):
    return csr_matrix(np.array([engineered_row(x) for x in texts], dtype=float))

def build_matrix(texts, vectorizer):
    return hstack([vectorizer.transform(texts), engineered_matrix(texts)]).tocsr()

def feature_names(vectorizer):
    return [f"word:{w}" for w in vectorizer.get_feature_names_out()] + [f"eng:{c}" for c in ENG_COLS]

def verdict(score):
    return "Low" if score < 30 else ("Medium" if score < 60 else "High")

def predict_and_explain(text, bundle, top_k=5):
    vec, model, names = bundle["vectorizer"], bundle["model"], bundle["names"]
    X = build_matrix([text], vec)
    prob = float(model.predict_proba(X)[0][1])
    score = round(prob * 100, 1)
    contribs = model.get_booster().predict(xgb.DMatrix(X), pred_contribs=True)[0][:-1]
    order = np.argsort(contribs)[::-1]
    reasons, words = [], []
    for i in order:
        if contribs[i] <= 0.05 or len(reasons) >= top_k:
            break
        n = names[i]
        if n.startswith("eng:"):
            reasons.append(f"{ENG_SENTENCES[n[4:]]} (+{contribs[i]:.2f} risk)")
        elif len(words) < 3:
            words.append(n[5:])
    if words:
        reasons.append("Contains wording common in fake postings: " + ", ".join(f"'{w}'" for w in words))
    if not reasons:
        reasons.append("No strong scam indicators found")
    return {"risk_score": score, "verdict": verdict(score), "reasons": reasons}
