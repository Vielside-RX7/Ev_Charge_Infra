"""
Authentication & JWT Utilities Module
====================================
Provides secure, zero-dependency password hashing (PBKDF2-HMAC-SHA256)
and standard HMAC-SHA256 JWT encoding/decoding for FastAPI.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any, Dict, Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import User

# Configuration
JWT_SECRET = os.getenv("JWT_SECRET", "voltguide-super-secret-production-ev-key-2026")
JWT_ALGORITHM = "HS256"
DEFAULT_TOKEN_EXPIRE_HOURS = 72


# ---------------------------------------------------------------------------
# Password Hashing (PBKDF2-HMAC-SHA256)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with random salt."""
    salt = secrets.token_hex(16)
    iterations = 100_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    hash_hex = dk.hex()
    return f"pbkdf2_sha256${iterations}${salt}${hash_hex}"


def verify_password(plain_password: str, hashed_password: Optional[str]) -> bool:
    """Verify plain password against hashed password string."""
    if not hashed_password or not plain_password:
        return False
    try:
        parts = hashed_password.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations = int(parts[1])
        salt = parts[2]
        expected_hash = parts[3]
        dk = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt.encode("utf-8"), iterations)
        return hmac.compare_digest(dk.hex(), expected_hash)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT Helpers (Pure Python HMAC-SHA256 Standard JWT)
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    padding = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s + padding)


def create_access_token(user_id: int, email: str, expires_hours: int = DEFAULT_TOKEN_EXPIRE_HOURS) -> str:
    """Generate a signed standard JWT token."""
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + (expires_hours * 3600),
    }

    header_bytes = json.dumps(header, separators=(",", ":")).encode("utf-8")
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    unsigned_token = f"{_b64url_encode(header_bytes)}.{_b64url_encode(payload_bytes)}"
    signature = hmac.new(JWT_SECRET.encode("utf-8"), unsigned_token.encode("utf-8"), hashlib.sha256).digest()

    return f"{unsigned_token}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify signature and expiration of JWT token; returns payload or None."""
    try:
        parts = token.strip().split(".")
        if len(parts) != 3:
            return None

        unsigned_token = f"{parts[0]}.{parts[1]}"
        expected_sig = hmac.new(JWT_SECRET.encode("utf-8"), unsigned_token.encode("utf-8"), hashlib.sha256).digest()
        actual_sig = _b64url_decode(parts[2])

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_bytes = _b64url_decode(parts[1])
        payload = json.loads(payload_bytes.decode("utf-8"))

        now = int(time.time())
        if payload.get("exp") and now > payload["exp"]:
            return None

        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# FastAPI Dependency Injections
# ---------------------------------------------------------------------------

def extract_token_from_header(auth_header: Optional[str]) -> Optional[str]:
    """Extracts bearer token from Authorization header."""
    if not auth_header:
        return None
    parts = auth_header.strip().split(" ")
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def get_current_user_optional(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Returns authenticated User if valid Bearer token provided, else None."""
    token = extract_token_from_header(authorization)
    if not token:
        return None
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        return None
    try:
        user_id = int(payload["sub"])
        user = db.query(User).filter(User.id == user_id).first()
        return user
    except Exception:
        return None


def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> User:
    """Enforces authentication and returns current User; raises 401 if unauthorized."""
    user = get_current_user_optional(authorization, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please provide a valid Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
