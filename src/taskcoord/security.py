from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([A-Za-z0-9._~+/-]+=*)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/-]+=*)"),
    re.compile(r"(?i)(claim_token[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)"),
    re.compile(r"(?i)(api_token[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)"),
    re.compile(r"(?i)(password[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)"),
    re.compile(r"(?i)(session_secret[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)"),
    re.compile(r"(?i)(cookie\s*[:=]\s*)([^\s;]+)"),
    re.compile(r"(?i)(taskcoord_session=)([^;\s]+)"),
)


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token(token: str, token_hash: str | None) -> bool:
    if not token or not token_hash:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)


def hash_password(password: str, iterations: int = 200_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations_s, salt_hex, digest_hex = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def request_fingerprint(method: str, path: str, body: bytes) -> str:
    material = method.upper().encode("ascii") + b"\n" + path.encode("utf-8") + b"\n" + body
    return hashlib.sha256(material).hexdigest()


def redact_text(message: str) -> str:
    redacted = message
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: match.group(1) + "[REDACTED]", redacted)
    return redacted
