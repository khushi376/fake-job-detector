# Fake Job Posting / Scam Detection Platform

An AI-powered web app that scores a pasted job posting for scam risk and explains why.

**Author:** Khushi (UID 24BDA70203), Department of Computer Science and Engineering, Chandigarh University

## What it does

1. The user pastes a job posting.
2. The backend scores it with an XGBoost model on TF-IDF text features plus engineered features (urgency words, payment requests, free-email contact, capital-letter ratio, and more).
3. A rule-based layer adds plain-English red flags for fees, requests for Aadhaar/bank/OTP details, and WhatsApp/Telegram or free-email contacts.
4. The app shows a 0-100 risk score, a verdict (Low / Medium / High), and the reasons.
5. The user can confirm or dispute the result. Feedback is saved to a database for future retraining.

## Model results

Dataset: Kaggle "Fake Job Postings" (EMSCAD), about 17,880 postings, about 5% fraudulent. Stratified 80/20 split, fixed seed, evaluated on the fraud class.

| Model | Precision | Recall | F1 | PR-AUC |
|---|---|---|---|---|
| Logistic Regression (baseline, TF-IDF only) | 0.633 | 0.896 | 0.742 | 0.885 |
| XGBoost + engineered features (primary) | 0.890 | 0.798 | 0.841 | 0.916 |

XGBoost confusion matrix on the test set: 3386 true negatives, 17 false positives, 35 false negatives, 138 true positives.

## Architecture

- `ml/`: feature engineering and explanation code (`ml_core.py`) and the trained model bundle
- `backend/`: FastAPI app with SQLite storage (`/predict`, `/feedback`, `/postings`, `/health`)
- `frontend/`: Streamlit app

## Run it locally

Backend (terminal 1):

    python3 -m venv venv
    source venv/bin/activate
    pip install -r backend/requirements.txt
    cd backend
    uvicorn app.main:app --reload

Frontend (terminal 2):

    python3 -m venv frontend/venv
    source frontend/venv/bin/activate
    pip install streamlit requests
    streamlit run frontend/app.py

Then open http://localhost:8501. API docs are at http://localhost:8000/docs.

On macOS, XGBoost needs the OpenMP library (`brew install libomp`).

## Implemented vs planned

Implemented: ML pipeline with baseline comparison, explainable predictions (engineered-feature contributions and rule-based reasons), REST API with Swagger docs, web UI, feedback storage.

Planned (not built yet): user login with JWT, admin dashboard and moderation queue, URL scraping, retraining from feedback, model registry, Docker and cloud deployment, multilingual support.

## Limitations

- The training data is mostly English and US-style postings, so accuracy on Indian job scams is untested. The rule layer covers common local patterns (registration fees, Aadhaar/bank requests, WhatsApp contacts).
- The rule layer is regex-based and can over-flag genuine postings, for example a real recruiter using a Gmail address.
- The reasons for word-based model features are less meaningful than the rule-based reasons.
- Metrics come from a single train/test split.
- Predictions are decision support, not proof that a posting is genuine or fake.
