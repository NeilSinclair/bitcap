"""Single-account authentication for the API.

One account, supplied by the environment. There is no users table, no signup
and no password reset, because there is exactly one operator and every one of
those would be machinery serving nobody (docs/decisions.md D39).

**Standard library only.** `hashlib.scrypt` is a memory-hard password KDF and
`hmac` gives signed, expiring session tokens; neither needs a dependency. A
project this size adding `passlib` and `pyjwt` to hash one password and sign one
token would be carrying a transitive stack for two function calls.

**What this does and does not protect.** The frontend is a static export, so its
HTML and JavaScript are public files — a login gate in the browser hides the UI,
it does not secure anything. Every protected route on this API therefore checks
the token server-side, which is where the guarantee actually lives: without a
valid token the API returns 401 and the page has nothing to render.

Generating the hash to put in `AUTH_PASSWORD_HASH`:

    uv run python -m api.auth

Environment:
    AUTH_EMAIL: The one account's email address.
    AUTH_PASSWORD_HASH: Output of the command above. Never the password itself.
    AUTH_SECRET: Random string signing session tokens. Rotating it logs
        everyone out, which is the intended panic button.
    AUTH_TOKEN_HOURS: Session lifetime, default 12.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Header, HTTPException

# scrypt cost parameters. n=2**14 with r=8, p=1 is the widely published
# interactive-login baseline: ~16 MB and ~100ms per verification, which is
# nothing for one login and expensive enough to make offline guessing of a
# leaked hash impractical. They are encoded into the hash string rather than
# assumed, so raising them later does not invalidate existing hashes.
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32
_SCHEME = "scrypt"


class AuthNotConfigured(RuntimeError):
    """Raised when the service is asked to authenticate with no account set.

    Deliberately distinct from a failed login: "nobody can log in because the
    deployment is missing AUTH_EMAIL" and "that password is wrong" are different
    problems, and collapsing them into 401 makes the first one undebuggable.
    """


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Hash a password for storage in `AUTH_PASSWORD_HASH`.

    Args:
        password: The plaintext password.
        salt: Explicit salt; generated randomly when omitted. Only tests pass one.

    Returns:
        `scrypt$n$r$p$salt_b64$hash_b64` — self-describing, so a hash made with
        today's cost parameters still verifies after they are raised.
    """
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return "$".join([
        _SCHEME, str(_N), str(_R), str(_P),
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    ])


def verify_password(password: str, encoded: str) -> bool:
    """Check a password against a stored hash.

    Args:
        password: The plaintext attempt.
        encoded: A string from :func:`hash_password`.

    Returns:
        True on a match. A malformed or unknown-scheme hash returns False
        rather than raising: a corrupted env var must fail closed, not 500.
    """
    try:
        scheme, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if scheme != _SCHEME:
            return False
        expected = base64.b64decode(hash_b64)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    # Constant time: a timing-variable comparison leaks the digest byte by byte.
    return hmac.compare_digest(actual, expected)


def _secret() -> bytes:
    secret = os.environ.get("AUTH_SECRET")
    if not secret:
        raise AuthNotConfigured("AUTH_SECRET is not set")
    return secret.encode()


def _b64(raw: bytes) -> str:
    """URL-safe base64 with padding stripped, so a token survives a query string."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(email: str, *, hours: float | None = None, now: float | None = None) -> str:
    """Mint a signed, expiring session token.

    Args:
        email: The authenticated account.
        hours: Lifetime; defaults to `AUTH_TOKEN_HOURS` or 12.
        now: Injectable clock for the tests.

    Returns:
        `payload_b64.signature_b64`. The payload is readable by anyone — it
        carries no secret, and the signature is what makes it unforgeable.
    """
    if hours is None:
        hours = float(os.environ.get("AUTH_TOKEN_HOURS", 12))
    payload = json.dumps(
        {"sub": email, "exp": (now or time.time()) + hours * 3600},
        separators=(",", ":"), sort_keys=True,
    ).encode()
    body = _b64(payload)
    signature = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def read_token(token: str, *, now: float | None = None) -> str | None:
    """Verify a token and return the email it was issued to.

    Args:
        token: A string from :func:`issue_token`.
        now: Injectable clock for the tests.

    Returns:
        The email, or None if the token is malformed, unsigned, tampered with,
        or expired. One return value for every failure on purpose: telling a
        caller *why* their token was rejected tells an attacker the same thing.
    """
    try:
        body, signature = token.split(".")
        expected = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(signature), expected):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError, AuthNotConfigured):
        return None
    if float(payload.get("exp", 0)) <= (now or time.time()):
        return None
    return payload.get("sub")


def login(email: str, password: str) -> str:
    """Authenticate the one account and issue a token.

    Args:
        email: Submitted email.
        password: Submitted password.

    Returns:
        A session token.

    Raises:
        AuthNotConfigured: No account is configured on this deployment.
        HTTPException: 401 on a bad email or password.
    """
    configured_email = os.environ.get("AUTH_EMAIL")
    configured_hash = os.environ.get("AUTH_PASSWORD_HASH")
    if not configured_email or not configured_hash:
        raise AuthNotConfigured("AUTH_EMAIL and AUTH_PASSWORD_HASH must both be set")

    # Both checks always run. Returning early on an unknown email makes the
    # response measurably faster than a wrong password, which turns the endpoint
    # into an oracle for whether an address is the configured one.
    email_ok = hmac.compare_digest(email.strip().lower(), configured_email.strip().lower())
    password_ok = verify_password(password, configured_hash)
    if not (email_ok and password_ok):
        raise HTTPException(status_code=401, detail="invalid email or password")
    return issue_token(configured_email)


def require_auth(authorization: str = Header(default="")) -> str:
    """FastAPI dependency: reject anything without a valid session token.

    Args:
        authorization: The `Authorization` header, expected as `Bearer <token>`.

    Returns:
        The authenticated email, so a route can use it.

    Raises:
        HTTPException: 401 with a `WWW-Authenticate` header when the token is
            missing or invalid; 503 when the deployment has no account
            configured, which is an operator error rather than a caller error.
    """
    if not os.environ.get("AUTH_SECRET"):
        raise HTTPException(status_code=503, detail="authentication is not configured")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401, detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    email = read_token(token)
    if email is None:
        raise HTTPException(
            status_code=401, detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return email


if __name__ == "__main__":
    import getpass

    entered = getpass.getpass("Password: ")
    if entered != getpass.getpass("Confirm: "):
        raise SystemExit("passwords do not match")
    print()
    print("Set these on the API service (never commit them):")
    print()
    print(f"  AUTH_PASSWORD_HASH={hash_password(entered)}")
    print(f"  AUTH_SECRET={secrets.token_urlsafe(32)}")
