"use client";

// Session handling shared by every page.
//
// Reading is public: every page renders for anyone. Signing in unlocks the two
// things that change state — starting a pipeline run and clearing the health
// badge. This is the one place that knows how a token is obtained, stored, sent
// and discarded.
//
// What this is and is not. The site is a static export, so greying a button out
// here is ergonomics. The control is that the API returns 401 on every
// non-GET route without a valid token.
//
// The token lives in localStorage rather than a cookie because the API is on a
// different origin from the static site; a cookie would need SameSite=None and
// credentialed CORS to travel at all, which is more moving parts for the same
// result on a single-operator tool.

import { useEffect, useState } from "react";

export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const KEY = "bitcap.token";
const ACCENT = "#5ac3f0";
const NEGATIVE = "#f2545b";

export function getToken() {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    // Private browsing and blocked site data both throw on access rather than
    // returning null. Treated as "not signed in", which is the safe reading.
    return null;
  }
}

function setToken(token) {
  try {
    if (token) window.localStorage.setItem(KEY, token);
    else window.localStorage.removeItem(KEY);
  } catch {
    /* nothing to do; the session just will not survive a reload */
  }
}

export function signOut() {
  setToken(null);
  if (typeof window !== "undefined") window.location.reload();
}

// A 401 mid-session means the token expired while the tab was open. Clearing it
// and reloading puts the login form up, which is better than every panel on the
// page failing separately with no explanation.
function onUnauthorised() {
  setToken(null);
  if (typeof window !== "undefined") window.location.reload();
}

export async function apiFetch(path, options = {}) {
  const token = getToken();
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  });
  // Only when a token was sent. A 401 with none is a wrong password on the
  // login form, and reloading there closed the form without saying why.
  if (response.status === 401 && token) {
    onUnauthorised();
    throw new Error("session expired");
  }
  if (!response.ok) {
    // The API puts a human-readable reason in `detail`; surfacing the status
    // code alone turns "select at least one leg" into "400".
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* not JSON; the status code is all there is */
    }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function LoginScreen({ onSignedIn, onCancel }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = await apiFetch("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setToken(body.token);
      onSignedIn();
    } catch (e) {
      setError(String(e.message || e));
      setBusy(false);
    }
  }

  return (
    <div className="app" style={{ position: "fixed", inset: 0, zIndex: 50, background: "var(--bg)" }}>
      <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
        <form onSubmit={submit} style={{ width: 380, display: "flex", flexDirection: "column", gap: 28 }}>
          <div className="label-bracket">Operator sign-in</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div className="serif" style={{ fontSize: 26, fontWeight: 500, lineHeight: 1.2 }}>
              Frontier Lab Intelligence
            </div>
            <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>
              Everything here is readable without an account. Signing in is only for
              starting pipeline runs.
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span className="label-bracket">Email</span>
              <input
                className="field-input"
                type="email"
                autoComplete="username"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span className="label-bracket">Password</span>
              <input
                className="field-input"
                type="password"
                autoComplete="current-password"
                placeholder="••••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {error ? (
              <div style={{ fontSize: 12, color: NEGATIVE, lineHeight: 1.5 }}>{error}</div>
            ) : null}
            <button
              className="btn"
              type="submit"
              disabled={busy}
              style={{
                padding: 12,
                background: ACCENT,
                color: "#0d0d0d",
                borderColor: ACCENT,
                opacity: busy ? 0.6 : 1,
              }}
            >
              {busy ? "Signing in…" : "Sign in"}
            </button>
            <button className="btn btn-ghost" type="button" style={{ padding: 12 }} onClick={onCancel}>
              Back
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// "checking" until the stored token has been validated against the API: a
// token in localStorage may have expired since it was issued. Then "in" or
// "out". Pages render either way; this only decides what is enabled.
export function useSession() {
  const [state, setState] = useState("checking");

  useEffect(() => {
    if (!getToken()) {
      setState("out");
      return;
    }
    fetch(`${API}/api/auth/me`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => setState(r.ok ? "in" : "out"))
      .catch(() => setState("out"));
  }, []);

  return state;
}

// The nav's last button: "Sign out" for the operator, "Sign in" for everyone
// else. Signing in reloads, so every `useSession` on the page agrees.
export function SessionButton() {
  const session = useSession();
  const [open, setOpen] = useState(false);

  if (open) {
    return (
      <LoginScreen
        onSignedIn={() => window.location.reload()}
        onCancel={() => setOpen(false)}
      />
    );
  }
  if (session === "in") {
    return <button className="btn btn-ghost" style={{ padding: "8px 14px" }} onClick={signOut}>Sign out</button>;
  }
  return <button className="btn btn-ghost" style={{ padding: "8px 14px" }} onClick={() => setOpen(true)}>Sign in</button>;
}
