import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from jose import jwt

from app.config import settings

ALGORITHM = "HS256"


def create_access_token(user_id: str, email: str, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=settings.JWT_EXPIRE_HOURS),
        "jti": secrets.token_hex(16),  # unique per token
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Raise jose.JWTError if the token is invalid or expired."""
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])


def create_refresh_token() -> tuple[str, str]:
    """Return (raw_token, token_hash). Raw goes to the client; hash is stored in DB."""
    raw = f"rt_{secrets.token_hex(32)}"
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
