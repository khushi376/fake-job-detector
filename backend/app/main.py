"""
Fake-Job Detector – FastAPI backend
"""

from __future__ import annotations

import json
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import joblib
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# ---------------------------------------------------------------------------
# Paths (all relative to this file – no hard-coded absolute paths)
# ---------------------------------------------------------------------------

BASE_DIR   = Path(__file__).resolve().parent          # backend/app/
ML_DIR     = BASE_DIR.parent.parent / "ml"            # <project-root>/ml/
MODEL_PATH = ML_DIR / "models" / "model_bundle.joblib"
DB_PATH    = BASE_DIR.parent / "app.db"               # backend/app.db

# ---------------------------------------------------------------------------
# SQLAlchemy setup
# ---------------------------------------------------------------------------

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class Prediction(Base):
    __tablename__ = "predictions"

    id         = Column(Integer,  primary_key=True, index=True)
    text       = Column(Text,     nullable=False)
    risk_score = Column(Float,    nullable=False)
    verdict    = Column(String,   nullable=False)
    reasons    = Column(Text,     nullable=False)   # JSON-encoded list[str]
    timestamp  = Column(DateTime, default=datetime.utcnow)


class Feedback(Base):
    __tablename__ = "feedback"

    id              = Column(Integer,  primary_key=True, index=True)
    posting_text    = Column(Text,     nullable=False)
    predicted_score = Column(Float,    nullable=False)
    user_label      = Column(String,   nullable=False)   # 'genuine' | 'scam'
    timestamp       = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Red-flag rule layer
# ---------------------------------------------------------------------------

_FEE_RE = re.compile(
    r"\b(registration fee|security deposit|processing fee|training fee|"
    r"pay |payment|send money|advance fee|kit charges|refundable)\b",
    re.IGNORECASE,
)
_PERSONAL_RE = re.compile(
    r"\b(aadhaar|aadhar|aadhar card|bank details?|account number|otp|"
    r"ifsc|pan card|bank account|upi|cvv|credit card)\b",
    re.IGNORECASE,
)
_FREE_EMAIL_RE = re.compile(
    r"\b[\w.+-]+@(gmail|yahoo|hotmail|outlook|rediffmail|ymail)\.(com|in|co\.in)\b",
    re.IGNORECASE,
)
_MESSAGING_RE = re.compile(
    r"\b(whatsapp|telegram|wa\.me)\b",
    re.IGNORECASE,
)

_MIN_RISK_IF_REDFLAG = 70


def apply_red_flags(text: str, model_score: float) -> tuple[list[str], float]:
    """
    Check rule-based red flags.

    Returns:
        (red_flag_reasons, adjusted_score)
    If any flag fires, score is raised to at least _MIN_RISK_IF_REDFLAG (70).
    """
    red_reasons: list[str] = []

    if _FEE_RE.search(text):
        red_reasons.append(
            "Job asks applicant to pay a fee or make a payment — a common scam tactic."
        )
    if _PERSONAL_RE.search(text):
        red_reasons.append(
            "Requests sensitive personal or financial details (Aadhaar, bank info, or OTP)."
        )
    if _FREE_EMAIL_RE.search(text):
        red_reasons.append(
            "Contact uses a free email domain (e.g. Gmail, Yahoo) instead of a corporate one."
        )
    if _MESSAGING_RE.search(text):
        red_reasons.append(
            "Contact is via WhatsApp or Telegram — unusual for legitimate recruiters."
        )

    adjusted_score = (
        max(model_score, float(_MIN_RISK_IF_REDFLAG)) if red_reasons else model_score
    )
    return red_reasons, adjusted_score


# ---------------------------------------------------------------------------
# App-state holder (populated during lifespan startup)
# ---------------------------------------------------------------------------

class _AppState:
    bundle:     dict | None = None
    predict_fn: object      = None


app_state = _AppState()


# ---------------------------------------------------------------------------
# Lifespan: load model once and bootstrap DB
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Expose /ml directory to Python's import system
    ml_str = str(ML_DIR)
    if ml_str not in sys.path:
        sys.path.insert(0, ml_str)

    # 2. Import predict_and_explain AFTER sys.path is patched
    from ml_core import predict_and_explain  # noqa: PLC0415

    # 3. Load the model bundle exactly once
    app_state.bundle = joblib.load(MODEL_PATH)
    app_state.predict_fn = predict_and_explain

    # 4. Create DB tables (no-op if they already exist)
    Base.metadata.create_all(bind=engine)

    yield  # application serves requests here


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Fake Job Detector API",
    description=(
        "Detects fraudulent job postings using an XGBoost model "
        "combined with rule-based red-flag detection."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class PredictRequest(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text must not be empty")
        if len(v) > 20_000:
            raise ValueError("text must not exceed 20,000 characters")
        return v


class PredictResponse(BaseModel):
    risk_score: float
    verdict:    str
    reasons:    list[str]


class FeedbackRequest(BaseModel):
    posting_text:    str
    predicted_score: float
    user_label:      Literal["genuine", "scam"]


class PostingItem(BaseModel):
    id:         int
    text:       str
    risk_score: float
    verdict:    str
    reasons:    list[str]
    timestamp:  datetime


# ---------------------------------------------------------------------------
# Helper: derive verdict label from numeric score (mirrors ml_core.verdict)
# ---------------------------------------------------------------------------

def _verdict(score: float) -> str:
    if score < 30:
        return "Low"
    if score < 60:
        return "Medium"
    return "High"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Utility"])
def health():
    """Liveness probe — always returns HTTP 200 with status ok."""
    return {"status": "ok"}


@app.post("/predict", response_model=PredictResponse, tags=["Inference"])
def predict(req: PredictRequest):
    """
    Analyse a job-posting text.

    1. Runs the XGBoost model via predict_and_explain.
    2. Applies rule-based red-flag checks.
    3. Merges reasons: red-flag reasons first, then model reasons.
    4. Saves the final result to the database.
    5. Returns {risk_score, verdict, reasons}.
    """
    # --- ML inference ---
    result        = app_state.predict_fn(req.text, app_state.bundle)
    model_score:  float      = result["risk_score"]
    model_reasons: list[str] = result["reasons"]

    # --- Rule-based red-flag layer ---
    red_reasons, final_score = apply_red_flags(req.text, model_score)

    # Red-flag reasons FIRST, model reasons appended after
    all_reasons  = red_reasons + model_reasons
    final_verdict = _verdict(final_score)

    # --- Persist to SQLite ---
    db: Session = SessionLocal()
    try:
        row = Prediction(
            text=req.text,
            risk_score=final_score,
            verdict=final_verdict,
            reasons=json.dumps(all_reasons),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    return PredictResponse(
        risk_score=final_score,
        verdict=final_verdict,
        reasons=all_reasons,
    )


@app.post("/feedback", status_code=201, tags=["Feedback"])
def feedback(req: FeedbackRequest):
    """
    Save user-supplied label (genuine / scam) for a posting.
    """
    db: Session = SessionLocal()
    try:
        row = Feedback(
            posting_text=req.posting_text,
            predicted_score=req.predicted_score,
            user_label=req.user_label,
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    return {"message": "Feedback saved."}


@app.get("/postings", response_model=list[PostingItem], tags=["Postings"])
def get_postings(
    limit: Optional[int] = Query(default=50, ge=1, le=500, description="Max results to return"),
):
    """
    Return saved predictions where risk_score >= 60, newest first.

    Query parameters:
    - **limit**: number of results (1–500, default 50)
    """
    db: Session = SessionLocal()
    try:
        rows = (
            db.query(Prediction)
            .filter(Prediction.risk_score >= 60)
            .order_by(Prediction.timestamp.desc())
            .limit(limit)
            .all()
        )
        return [
            PostingItem(
                id=r.id,
                text=r.text,
                risk_score=r.risk_score,
                verdict=r.verdict,
                reasons=json.loads(r.reasons),
                timestamp=r.timestamp,
            )
            for r in rows
        ]
    finally:
        db.close()
