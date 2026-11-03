"""
Fake-Job Detector – FastAPI backend
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import joblib
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, field_validator
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.auth import (
    _get_current_user_from_token,
    create_access_token,
    get_jwt_secret,
    hash_password,
    oauth2_scheme,
    verify_password,
)

from app.url_fetch import UrlFetchError, fetch_posting_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths (all relative to this file – no hard-coded absolute paths)
# ---------------------------------------------------------------------------

BASE_DIR   = Path(__file__).resolve().parent          # backend/app/
ML_DIR     = BASE_DIR.parent.parent / "ml"            # <project-root>/ml/
MODEL_PATH = ML_DIR / "models" / "model_bundle.joblib"
DB_PATH    = BASE_DIR.parent / "app.db"               # backend/app.db
DOTENV_PATH = BASE_DIR.parent / ".env"                # backend/.env  (optional)

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


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id            = Column(Integer,  primary_key=True, index=True)
    email         = Column(String,   unique=True, nullable=False, index=True)
    password_hash = Column(String,   nullable=False)
    role          = Column(String,   nullable=False, default="user")   # 'user' | 'admin'
    created_at    = Column(DateTime, default=datetime.utcnow)


class Prediction(Base):
    __tablename__ = "predictions"

    id         = Column(Integer,  primary_key=True, index=True)
    text       = Column(Text,     nullable=False)
    risk_score = Column(Float,    nullable=False)
    verdict    = Column(String,   nullable=False)
    reasons    = Column(Text,     nullable=False)           # JSON-encoded list[str]
    user_id    = Column(Integer,  ForeignKey("users.id"), nullable=True)
    source_url = Column(String,   nullable=True)
    timestamp  = Column(DateTime, default=datetime.utcnow)


class Feedback(Base):
    __tablename__ = "feedback"

    id              = Column(Integer,  primary_key=True, index=True)
    posting_text    = Column(Text,     nullable=False)
    predicted_score = Column(Float,    nullable=False)
    user_label      = Column(String,   nullable=False)      # 'genuine' | 'scam'
    user_id         = Column(Integer,  ForeignKey("users.id"), nullable=True)
    status          = Column(String,   nullable=False, default="pending")  # pending|approved|rejected
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
# Email validation helper
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _validate_email(email: str) -> str:
    """Lowercase, strip, and validate email format. Raises ValueError on failure."""
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValueError("Invalid email address format.")
    return email


# ---------------------------------------------------------------------------
# Lifespan: env loading, model load, DB bootstrap, admin seed
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Load .env file if present (silent if missing)
    load_dotenv(DOTENV_PATH)

    # 2. Resolve JWT secret (logs warning if not set)
    get_jwt_secret()

    # 3. Expose /ml directory to Python's import system
    ml_str = str(ML_DIR)
    if ml_str not in sys.path:
        sys.path.insert(0, ml_str)

    # 4. Import predict_and_explain AFTER sys.path is patched
    from ml_core import predict_and_explain  # noqa: PLC0415

    # 5. Load the model bundle exactly once
    app_state.bundle = joblib.load(MODEL_PATH)
    app_state.predict_fn = predict_and_explain

    # 6. Create DB tables (no-op if they already exist)
    Base.metadata.create_all(bind=engine)

    # 7. Bootstrap admin user if env vars are set and no admin exists yet
    admin_email    = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if admin_email and admin_password:
        db: Session = SessionLocal()
        try:
            existing_admin = db.query(User).filter(User.role == "admin").first()
            if existing_admin is None:
                admin = User(
                    email=admin_email,
                    password_hash=hash_password(admin_password),
                    role="admin",
                )
                db.add(admin)
                db.commit()
                logger.info("Admin user created: %s", admin_email)
        finally:
            db.close()

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
    version="2.0.0",
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
# Auth dependencies
# ---------------------------------------------------------------------------

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Dependency: decode JWT and return the authenticated User row."""
    return _get_current_user_from_token(token, db, User)


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency: ensure the authenticated user has the 'admin' role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email:    str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email_field(cls, v: str) -> str:
        return _validate_email(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        if len(v.encode("utf-8")) > 72:
            raise ValueError(
                "Password must not exceed 72 bytes when UTF-8 encoded."
            )
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"


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
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/auth/register", status_code=201, tags=["Auth"])
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    """
    Create a new user account.  Role is always 'user'.

    - Email must be valid format; stored lowercase.
    - Password must be 8–72 bytes (UTF-8).
    - Returns 409 if the email is already registered.
    """
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        role="user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"message": "Account created.", "email": user.email}


@app.post("/auth/login", response_model=TokenResponse, tags=["Auth"])
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    db:   Session                   = Depends(get_db),
):
    """
    Authenticate with **username** (email) and **password** (form fields).
    Returns a Bearer JWT valid for 8 hours.

    Uses OAuth2PasswordRequestForm so Swagger's **Authorize** button works.
    """
    email = form.username.strip().lower()
    user  = db.query(User).filter(User.email == email).first()

    # Generic error — never reveal whether the email exists
    _auth_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if user is None or not verify_password(form.password, user.password_hash):
        raise _auth_error

    token = create_access_token(user.id, user.email, user.role)
    return TokenResponse(access_token=token)


# ---------------------------------------------------------------------------
# Utility endpoint
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Utility"])
def health():
    """Liveness probe — always returns HTTP 200 with status ok."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Inference endpoint  (login required)
# ---------------------------------------------------------------------------

@app.post("/predict", response_model=PredictResponse, tags=["Inference"])
def predict(
    req:          PredictRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Analyse a job-posting text. **Requires authentication.**

    1. Runs the XGBoost model via predict_and_explain.
    2. Applies rule-based red-flag checks.
    3. Merges reasons: red-flag reasons first, then model reasons.
    4. Saves the result (linked to the authenticated user) to the database.
    5. Returns {risk_score, verdict, reasons}.
    """
    # --- ML inference ---
    result         = app_state.predict_fn(req.text, app_state.bundle)
    model_score:   float      = result["risk_score"]
    model_reasons: list[str]  = result["reasons"]

    # --- Rule-based red-flag layer ---
    red_reasons, final_score = apply_red_flags(req.text, model_score)

    # Red-flag reasons FIRST, model reasons appended after
    all_reasons   = red_reasons + model_reasons
    final_verdict = _verdict(final_score)

    # --- Persist to SQLite ---
    db: Session = SessionLocal()
    try:
        row = Prediction(
            text=req.text,
            risk_score=final_score,
            verdict=final_verdict,
            reasons=json.dumps(all_reasons),
            user_id=current_user.id,
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


class UrlRequest(BaseModel):
    url: str


class UrlPredictResponse(PredictResponse):
    source_url: str
    text_preview: str


@app.post("/predict/url", response_model=UrlPredictResponse, tags=["Inference"])
def predict_url(
    req:          UrlRequest,
    current_user: User = Depends(get_current_user),
):
    # Fetch the page safely (SSRF-protected) and extract the posting text
    try:
        text = fetch_posting_text(req.url)
    except UrlFetchError as exc:
        raise HTTPException(status_code=422, detail=exc.message)

    result         = app_state.predict_fn(text, app_state.bundle)
    model_score:   float     = result["risk_score"]
    model_reasons: list[str] = result["reasons"]

    red_reasons, final_score = apply_red_flags(text, model_score)
    all_reasons   = red_reasons + model_reasons
    final_verdict = _verdict(final_score)

    db: Session = SessionLocal()
    try:
        row = Prediction(
            text=text,
            risk_score=final_score,
            verdict=final_verdict,
            reasons=json.dumps(all_reasons),
            user_id=current_user.id,
            source_url=req.url.strip(),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    return UrlPredictResponse(
        risk_score=final_score,
        verdict=final_verdict,
        reasons=all_reasons,
        source_url=req.url.strip(),
        text_preview=text[:300],
    )


# ---------------------------------------------------------------------------
# Feedback endpoint  (login required)
# ---------------------------------------------------------------------------

@app.post("/feedback", status_code=201, tags=["Feedback"])
def feedback(
    req:          FeedbackRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Save user-supplied label (genuine / scam) for a posting. **Requires authentication.**

    Feedback is stored with status='pending' and linked to the authenticated user.
    """
    db: Session = SessionLocal()
    try:
        row = Feedback(
            posting_text=req.posting_text,
            predicted_score=req.predicted_score,
            user_label=req.user_label,
            user_id=current_user.id,
            status="pending",
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    return {"message": "Feedback saved."}


# ---------------------------------------------------------------------------
# Postings endpoint  (public)
# ---------------------------------------------------------------------------

@app.get("/postings", response_model=list[PostingItem], tags=["Postings"])
def get_postings(
    q:      Optional[str] = Query(default=None, description="Keyword search over text and reasons"),
    limit:  Optional[int] = Query(default=50,   ge=1, le=500, description="Max results (default 50)"),
    offset: Optional[int] = Query(default=0,    ge=0,         description="Pagination offset (default 0)"),
    db:     Session       = Depends(get_db),
):
    """
    Return saved predictions where risk_score >= 60, newest first. **Public.**

    Query parameters:
    - **q**: optional keyword filter (case-insensitive, searches text and reasons columns)
    - **limit**: number of results (1–500, default 50)
    - **offset**: pagination offset (default 0)
    """
    query = (
        db.query(Prediction)
        .filter(Prediction.risk_score >= 60)
    )

    if q:
        pattern = f"%{q}%"
        query = query.filter(
            Prediction.text.ilike(pattern) | Prediction.reasons.ilike(pattern)
        )

    rows = (
        query
        .order_by(Prediction.timestamp.desc())
        .offset(offset)
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
