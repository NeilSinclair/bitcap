"use client";

// Session handling shared by every page.
//
// The whole site is behind the login, so this is the one place that knows how a
// token is obtained, stored, sent and discarded. Pages call `apiFetch` and wrap
// themselves in `<Gate>`; nothing else touches the token.
//
// What this is and is not. The site is a static export — the HTML and this file
// are public, and anyone can read them. The gate below hides the *interface*.
// The actual protection is that every route on the API returns 401 without a
// valid token, so an unauthenticated visitor can render the shell and still
// receive no data. Treat the gate as ergonomics, not as the control.
//
// The token lives in localStorage rather than a cookie because the API is on a
// different origin from the static site; a cookie would need SameSite=None and
// credentialed CORS to travel at all, which is more moving parts for the same
// result on a single-operator tool.

import { useCallback, useEffect, useState } from "react";

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
  if (response.status === 401) {
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

function LogoMark({ size = 86, fill = "#f5f4f1", accent = ACCENT }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} viewBox="0 0 72 36" fill="none">
      <path
        d="M66.3857 1.36043L58.1165 34.525H1.27521L9.54438 1.36043H66.3857ZM67.6062 0.383301H8.81065L8.56725 1.36043L0.298081 34.525L0.0546875 35.5022H58.8503L59.0937 34.525L67.3628 1.36043L67.6062 0.383301Z"
        fill={fill}
      />
      <path d="M23.5 10.5H31.5L28 25.5H20L23.5 10.5Z" fill={accent} />
    </svg>
  );
}

function LoginScreen({ onSignedIn }) {
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
    <div className="app">
      <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
        <form onSubmit={submit} style={{ width: 380, display: "flex", flexDirection: "column", gap: 28 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 16, alignItems: "flex-start" }}>
            <LogoMark size={86} />
            <div className="label-bracket">BIT Capital · internal</div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div className="serif" style={{ fontSize: 26, fontWeight: 500, lineHeight: 1.2 }}>
              Frontier Lab Intelligence
            </div>
            <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>
              Sourced signal on the frontier labs, scored and routed to what it means for the
              book and for how we&apos;d build.
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span className="label-bracket">Email</span>
              <input
                className="field-input"
                type="email"
                autoComplete="username"
                placeholder="you@bitcap.com"
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
          </div>
        </form>
      </div>
    </div>
  );
}

export function Gate({ children }) {
  // "checking" until the stored token has been validated against the API. A
  // token in localStorage may have expired since it was issued, and rendering
  // the page before finding out means every panel fails at once instead of the
  // login form appearing.
  const [state, setState] = useState("checking");

  const check = useCallback(() => {
    if (!getToken()) {
      setState("out");
      return;
    }
    fetch(`${API}/api/auth/me`, { headers: { Authorization: `Bearer ${getToken()}` } })
      .then((r) => setState(r.ok ? "in" : "out"))
      .catch(() => setState("out"));
  }, []);

  useEffect(check, [check]);

  if (state === "checking") {
    return (
      <div className="app">
        <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--muted-2)", fontSize: 13 }}>
          Checking session…
        </div>
      </div>
    );
  }
  if (state === "out") return <LoginScreen onSignedIn={() => setState("in")} />;
  return children;
}
