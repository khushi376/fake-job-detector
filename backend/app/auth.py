"""
auth.py – JWT + bcrypt authentication helpers for the Fake-Job Detector API.

Password hashing: bcrypt package (hashpw / checkpw) – NOT passlib.
Token signing:    PyJWT
Secret loading:   python-dotenv  →  JWT_SECRET env var
                  Falls back to a random 32-byte hex secret with a warning.
"""

from __future__ import annotations

import logging
import os
import secrets

import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JWT configuration
# ---------------------------------------------------------------------------

_JWT_ALGORITHM = "HS256"
_TOKEN_EXPIRE_HOURS = 8

# Resolved once at import time (after dotenv has been loaded in lifespan).
# We defer the actual read to a function so that load_dotenv() in lifespan
# runs first.
_jwt_secret: str | None = None


def get_jwt_secret() -> str:
    """Return the JWT secret, generating a random one if not configured."""
    global _jwt_secret
    if _jwt_secret is None:
        secret = os.environ.get("JWT_SECRET", "").strip()
        if not secret:
            secret = secrets.token_hex(32)
            logger.warning(
                "JWT_SECRET is not set in the environment. "
                "A random secret has been generated for this session. "
                "All tokens will be invalidated on restart. "
                "Set JWT_SECRET in your .env file for production use."
            )
        _jwt_secret = secret
    return _jwt_secret


# ---------------------------------------------------------------------------
# Password helpers  (bcrypt directly – no passlib)
# ---------------------------------------------------------------------------

_MAX_BCRYPT_BYTES = 72   # bcrypt silently truncates beyond 72 bytes


def hash_password(plain: str) -> str:
    """Hash *plain* with bcrypt and return the digest as a str."""
    encoded = plain.encode("utf-8")
    if len(encoded) > _MAX_BCRYPT_BYTES:
        raise ValueError(
            f"Password must not exceed {_MAX_BCRYPT_BYTES} bytes when UTF-8 encoded."
        )
    hashed = bcrypt.hashpw(encoded, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches the stored *hashed* bcrypt digest."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(user_id: int, email: str, role: str) -> str:
    """Encode a signed JWT valid for _TOKEN_EXPIRE_HOURS hours."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=_TOKEN_EXPIRE_HOURS),
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """
    Decode and verify *token*.  Raises jwt.PyJWTError on failure
    (expired, invalid signature, malformed, etc.).
    """
    return jwt.decode(token, get_jwt_secret(), algorithms=[_JWT_ALGORITHM])


# ---------------------------------------------------------------------------
# FastAPI security scheme
# ---------------------------------------------------------------------------

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ---------------------------------------------------------------------------
# Dependencies  (imported into main.py)
# ---------------------------------------------------------------------------

# NOTE: The User ORM model lives in main.py to avoid a circular import.
# We accept the Session and User class as parameters injected at call-site
# via thin wrapper dependencies defined in main.py.

def _get_current_user_from_token(token: str, db: Session, user_model):
    """
    Core logic: decode token, look up the user row, return it.
    Raises HTTP 401 on any failure.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exc
    except jwt.PyJWTError:
        raise credentials_exc

    user = db.query(user_model).filter(user_model.id == int(user_id)).first()
    if user is None:
        raise credentials_exc
    return user
