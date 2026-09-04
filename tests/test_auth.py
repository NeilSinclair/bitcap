"""Single-account auth: hashing, tokens, and what happens when it is misconfigured.

The silent failures this suite exists to catch:

* **A route that forgot its dependency.** The whole point of gating the site is
  that no data route answers without a token; one route missing
  `Depends(require_auth)` leaves the database readable and looks fine in a
  browser that is logged in. `test_api_gate` enumerates the app's own routes
  rather than a hand-written list, so a *new* route is covered the day it lands.
* **Auth that fails open.** A missing `AUTH_SECRET`, a corrupted hash, a
  tampered token — each must deny, and none may 500.
* **A token that outlives its expiry**, which would make the session length a
  decoration.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import auth

PASSWORD = "correct horse battery staple"
EMAIL = "neil@example.com"


@pytest.fixture()
def configured(monkeypatch):
    """A deployment with the one account set up."""
    monkeypatch.setenv("AUTH_EMAIL", EMAIL)
    monkeypatch.setenv("AUTH_PASSWORD_HASH", auth.hash_password(PASSWORD))
    monkeypatch.setenv("AUTH_SECRET", "test-secret-not-a-real-one")
    monkeypatch.delenv("AUTH_TOKEN_HOURS", raising=False)


class TestPasswordHashing:
    def test_a_password_verifies_against_its_own_hash(self):
        assert auth.verify_password(PASSWORD, auth.hash_password(PASSWORD))

    def test_a_wrong_password_does_not(self):
        assert not auth.verify_password("wrong", auth.hash_password(PASSWORD))

    def test_the_same_password_hashes_differently_every_time(self):
        """A per-hash salt: identical passwords must not produce identical hashes."""
        assert auth.hash_password(PASSWORD) != auth.hash_password(PASSWORD)

    def test_the_hash_never_contains_the_password(self):
        assert PASSWORD not in auth.hash_password(PASSWORD)

    def test_the_cost_parameters_are_stored_in_the_hash(self):
        """Self-describing, so raising the cost later does not invalidate old hashes."""
        scheme, n, r, p, _salt, _digest = auth.hash_password(PASSWORD).split("$")
        assert scheme == "scrypt"
        assert (int(n), int(r), int(p)) == (auth._N, auth._R, auth._P)

    @pytest.mark.parametrize("corrupt", [
        "", "not-a-hash", "scrypt$broken", "bcrypt$1$2$3$4$5",
        "scrypt$notanint$8$1$c2FsdA==$aGFzaA==",
    ])
    def test_a_corrupted_hash_denies_rather_than_raising(self, corrupt):
        """A mangled env var must fail closed, not 500 on every login attempt."""
        assert auth.verify_password(PASSWORD, corrupt) is False


class TestTokens:
    def test_a_fresh_token_reads_back_its_email(self, configured):
        assert auth.read_token(auth.issue_token(EMAIL)) == EMAIL

    def test_an_expired_token_is_rejected(self, configured):
        token = auth.issue_token(EMAIL, hours=1, now=time.time() - 7200)
        assert auth.read_token(token) is None

    def test_a_token_signed_with_another_secret_is_rejected(self, configured, monkeypatch):
        token = auth.issue_token(EMAIL)
        monkeypatch.setenv("AUTH_SECRET", "a-different-secret")
        assert auth.read_token(token) is None

    def test_editing_the_payload_invalidates_the_signature(self, configured):
        """The payload is readable by design; the signature is what makes it safe."""
        body, signature = auth.issue_token(EMAIL).split(".")
        payload = json.loads(auth._unb64(body))
        payload["exp"] = time.time() + 10**6  # a century-long session
        forged = auth._b64(json.dumps(payload, separators=(",", ":"),
                                      sort_keys=True).encode())
        assert auth.read_token(f"{forged}.{signature}") is None

    @pytest.mark.parametrize("junk", ["", "nodot", "a.b.c", "...", "!!!.???"])
    def test_malformed_tokens_are_rejected_without_raising(self, configured, junk):
        assert auth.read_token(junk) is None

    def test_no_secret_configured_denies_rather_than_raising(self, monkeypatch):
        monkeypatch.delenv("AUTH_SECRET", raising=False)
        assert auth.read_token("anything.atall") is None


class TestLogin:
    def test_the_right_credentials_return_a_usable_token(self, configured):
        assert auth.read_token(auth.login(EMAIL, PASSWORD)) == EMAIL

    def test_email_is_matched_case_insensitively(self, configured):
        assert auth.read_token(auth.login(EMAIL.upper(), PASSWORD)) == EMAIL

    def test_a_wrong_password_is_401(self, configured):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            auth.login(EMAIL, "wrong")
        assert exc.value.status_code == 401

    def test_an_unknown_email_is_401(self, configured):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            auth.login("someone@else.com", PASSWORD)
        assert exc.value.status_code == 401

    def test_the_same_message_for_bad_email_and_bad_password(self, configured):
        """Distinct messages tell an attacker which half they got right."""
        from fastapi import HTTPException

        messages = set()
        for email, password in [(EMAIL, "wrong"), ("nobody@x.com", PASSWORD)]:
            with pytest.raises(HTTPException) as exc:
                auth.login(email, password)
            messages.add(exc.value.detail)
        assert len(messages) == 1

    @pytest.mark.parametrize("email", ["nëil@example.com", "ü@x.com", "日本@x.com"])
    def test_a_non_ascii_email_is_401_not_500(self, configured, email):
        """`hmac.compare_digest` raises TypeError on a non-ASCII str.

        Live: `POST /api/auth/login {"email": "nëil@example.com"}` returned 500
        rather than the 401 the docstring promises, and an internationalised
        `AUTH_EMAIL` made every login fail the same way (D43).
        """
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            auth.login(email, PASSWORD)
        assert exc.value.status_code == 401

    def test_a_non_ascii_configured_email_still_works(self, monkeypatch):
        """The other half: an IDN account must be able to log in at all."""
        monkeypatch.setenv("AUTH_EMAIL", "nëil@example.com")
        monkeypatch.setenv("AUTH_PASSWORD_HASH", auth.hash_password(PASSWORD))
        monkeypatch.setenv("AUTH_SECRET", "test-secret")
        assert auth.read_token(auth.login("nëil@example.com", PASSWORD)) == "nëil@example.com"

    def test_no_account_configured_is_distinguishable_from_a_bad_password(self, monkeypatch):
        """503 not 401: 'the deployment is broken' is not 'you typed it wrong'."""
        monkeypatch.delenv("AUTH_EMAIL", raising=False)
        monkeypatch.delenv("AUTH_PASSWORD_HASH", raising=False)
        with pytest.raises(auth.AuthNotConfigured):
            auth.login(EMAIL, PASSWORD)


class TestLoginIsRateLimited:
    """The silent failure this catches: an unthrottled scrypt on the open route.

    `login` is the one route reachable without a credential and it burns ~100ms
    of CPU and 16 MB per attempt. Unthrottled it is both a password oracle to
    grind and a way to exhaust a small instance with a loop (D44).
    """

    @pytest.fixture(autouse=True)
    def _clear(self):
        auth._attempts.clear()
        yield
        auth._attempts.clear()

    def test_repeated_failures_from_one_source_are_cut_off(self, configured):
        from fastapi import HTTPException

        for _ in range(auth._MAX_ATTEMPTS):
            with pytest.raises(HTTPException):
                auth.login(EMAIL, "wrong", source="10.0.0.1")

        with pytest.raises(auth.TooManyAttempts):
            auth.login(EMAIL, "wrong", source="10.0.0.1")

    def test_a_different_source_is_unaffected(self, configured):
        from fastapi import HTTPException

        for _ in range(auth._MAX_ATTEMPTS + 5):
            with pytest.raises((HTTPException, auth.TooManyAttempts)):
                auth.login(EMAIL, "wrong", source="10.0.0.1")

        with pytest.raises(HTTPException) as exc:
            auth.login(EMAIL, "wrong", source="10.0.0.2")
        assert exc.value.status_code == 401, "one attacker must not lock out everyone"

    def test_a_correct_password_is_never_counted(self, configured):
        """Rate limiting is for guessing; locking the operator out is the thing to avoid."""
        from fastapi import HTTPException

        for _ in range(auth._MAX_ATTEMPTS - 1):
            with pytest.raises(HTTPException):
                auth.login(EMAIL, "wrong", source="10.0.0.3")

        for _ in range(20):
            assert auth.login(EMAIL, PASSWORD, source="10.0.0.3")

    def test_the_window_expires(self, configured):
        from fastapi import HTTPException

        past = time.time() - auth._WINDOW_SECONDS - 1
        auth._attempts["10.0.0.4"] = [past] * (auth._MAX_ATTEMPTS + 5)

        with pytest.raises(HTTPException) as exc:
            auth.login(EMAIL, "wrong", source="10.0.0.4")
        assert exc.value.status_code == 401, "stale attempts must age out"


def _gated_app() -> FastAPI:
    from fastapi import Depends

    app = FastAPI()

    @app.get("/open")
    def open_route():
        return {"ok": True}

    @app.get("/closed", dependencies=[Depends(auth.require_auth)])
    def closed_route():
        return {"secret": True}

    return app


class TestRequireAuth:
    def test_no_header_is_401(self, configured):
        assert TestClient(_gated_app()).get("/closed").status_code == 401

    def test_a_valid_token_is_let_through(self, configured):
        token = auth.issue_token(EMAIL)
        r = TestClient(_gated_app()).get(
            "/closed", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200

    @pytest.mark.parametrize("header", [
        "Bearer", "Bearer ", "Basic abc123", "abc123", "Bearer not.a.token",
    ])
    def test_bad_authorization_headers_are_401(self, configured, header):
        r = TestClient(_gated_app()).get("/closed", headers={"Authorization": header})
        assert r.status_code == 401

    def test_the_401_names_the_scheme(self, configured):
        """Without WWW-Authenticate a client cannot tell what to send."""
        r = TestClient(_gated_app()).get("/closed")
        assert r.headers.get("WWW-Authenticate") == "Bearer"

    def test_unconfigured_auth_is_503_not_a_silent_pass(self, monkeypatch):
        monkeypatch.delenv("AUTH_SECRET", raising=False)
        assert TestClient(_gated_app()).get("/closed").status_code == 503

    def test_an_expired_token_stops_working(self, configured):
        token = auth.issue_token(EMAIL, hours=1, now=time.time() - 7200)
        r = TestClient(_gated_app()).get(
            "/closed", headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 401
