import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash plaintext password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plaintext password against bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    subject: str,
    user_type: str,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Generate a signed JWT access token.

    Every token carries a unique `jti` (JWT ID) claim so that individual
    tokens can be blacklisted in Redis on logout without waiting for natural
    expiry.
    """
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))

    to_encode: Dict[str, Any] = {
        "sub": subject,
        "user_type": user_type,   # surface identifier (customer / employee / admin)
        "token_type": "access",
        "jti": str(uuid.uuid4()),  # unique token ID — used for blacklisting
        "iat": now,
        "exp": expire,
    }
    if extra_claims:
        to_encode.update(extra_claims)

    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(
    subject: str,
    user_type: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Generate a signed JWT refresh token.

    Also carries a `jti` so that specific refresh tokens can be revoked.
    The `token_type` claim is ``"refresh"`` — validated in auth_service to
    prevent access tokens being used as refresh tokens and vice-versa.
    """
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))

    to_encode: Dict[str, Any] = {
        "sub": subject,
        "user_type": user_type,
        "token_type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": expire,
    }

    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Decode and cryptographically validate a JWT token.

    Returns the full payload dict on success, or ``None`` if the token is
    malformed, expired, or has an invalid signature.  The caller is
    responsible for any additional checks (blacklist, token_type, etc.).
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except JWTError:
        return None


def token_remaining_seconds(payload: Dict[str, Any]) -> int:
    """
    Return the number of whole seconds until *payload['exp']* elapses.
    Returns 0 if already expired.  Used to set the Redis blacklist TTL so
    entries expire automatically and don't accumulate indefinitely.
    """
    exp = payload.get("exp")
    if not exp:
        return 0
    remaining = int(exp) - int(datetime.now(timezone.utc).timestamp())
    return max(0, remaining)
