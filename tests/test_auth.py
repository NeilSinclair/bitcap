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

    def test_no_account_configured_is_distinguishable_from_a_bad_password(self, monkeypatch):
        """503 not 401: 'the deployment is broken' is not 'you typed it wrong'."""
        monkeypatch.delenv("AUTH_EMAIL", raising=False)
        monkeypatch.delenv("AUTH_PASSWORD_HASH", raising=False)
        with pytest.raises(auth.AuthNotConfigured):
            auth.login(EMAIL, PASSWORD)


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
