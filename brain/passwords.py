import base64
import hashlib
import hmac
import os
import secrets
from typing import Optional


_DEFAULT_ITERATIONS = 200_000


def _iterations() -> int:
    raw = os.getenv("PASSWORD_HASH_ITERATIONS", str(_DEFAULT_ITERATIONS)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = _DEFAULT_ITERATIONS
    return max(50_000, min(value, 1_000_000))


def hash_password(password: str) -> str:
    pw = str(password or "")
    if not pw:
        raise ValueError("Password is empty")
    salt = secrets.token_bytes(16)
    iterations = _iterations()
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, iterations)
    salt_b64 = base64.urlsafe_b64encode(salt).decode().rstrip("=")
    dk_b64 = base64.urlsafe_b64encode(dk).decode().rstrip("=")
    return f"pbkdf2_sha256${iterations}${salt_b64}${dk_b64}"


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    token = str(password_hash)
    parts = token.split("$")
    if len(parts) != 4:
        return False
    scheme, iter_raw, salt_b64, dk_b64 = parts
    if scheme != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iter_raw)
    except ValueError:
        return False

    padded_salt = salt_b64 + "=" * (-len(salt_b64) % 4)
    padded_dk = dk_b64 + "=" * (-len(dk_b64) % 4)
    try:
        salt = base64.urlsafe_b64decode(padded_salt.encode())
        expected = base64.urlsafe_b64decode(padded_dk.encode())
    except Exception:
        return False

    dk = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk, expected)

