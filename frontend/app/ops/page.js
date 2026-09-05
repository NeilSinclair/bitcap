"use client";

// Operations view: is the pipeline working, what did it cost, and is the
// classifier still agreeing with itself.
//
// A separate route rather than a tab inside the dashboard. The dashboard answers
// "what did we learn"; this answers "can I trust what the dashboard is showing",
// and the two have different readers. Keeping it here also leaves page.js as the
// single-component prototype it was deliberately built as.

import { useEffect, useState } from "react";

import { Gate, apiFetch, signOut } from "../auth";

const ACCENT = "#5ac3f0";
const NEGATIVE = "#f2545b";
const MUTED = "#9a9992";

function statusColor(status) {
  if (status === "succeeded") return ACCENT;
  if (status === "failed") return NEGATIVE;
  return MUTED;
}

function ago(iso) {
  if (!iso) return "—";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}

function Section({ title, note, children }) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="label-bracket">{title}</span>
        {note ? <div style={{ fontSize: 12, color: "var(--muted-2)" }}>{note}</div> : null}
      </div>
      {children}
    </section>
  );
}

function Tile({ label, value, sub, tone }) {
  return (
    <div style={{ flex: "1 1 160px", minWidth: 160, border: "1px solid var(--border)", background: "var(--bg-2)", padding: "14px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
      <span className="label-bracket">{label}</span>
      <div className="serif" style={{ fontSize: 24, lineHeight: 1.1, color: tone || "var(--text)" }}>{value}</div>
      {sub ? <div style={{ fontSize: 11, color: "var(--muted-2)" }}>{sub}</div> : null}
    </div>
  );
}

// One series (mechanism agreement) against a fixed threshold — so no legend
// box: the title names the series, and the only other line is labelled inline.
// Below two points there is nothing to plot, and a stat tile is the honest
// form; a two-pixel line dressed as a trend would imply history that does not
// exist yet.
function DriftChart({ points, floor = 0.8 }) {
  const w = 640;
  const h = 160;
  const pad = { top: 12, right: 16, bottom: 22, left: 34 };
  const usable = points.filter((p) => p.mechanism_f1 !== null && p.mechanism_f1 !== undefined);

  if (usable.length < 2) {
    const only = usable[0];
    return (
      <div style={{ border: "1px solid var(--border)", background: "var(--bg-2)", padding: 16, fontSize: 12, color: "var(--muted)" }}>
        {only ? (
          <>
            Agreement <span style={{ color: only.mechanism_f1 < floor ? NEGATIVE : ACCENT, fontSize: 18 }} className="serif">{only.mechanism_f1.toFixed(3)}</span>{" "}
            against a floor of {floor.toFixed(2)}, over {only.compared} gold items.
            <div style={{ marginTop: 6, color: "var(--muted-2)" }}>
              One measurement so far — a trend line needs at least two.
            </div>
          </>
        ) : (
          "No drift measurements recorded yet."
        )}
      </div>
    );
  }

  // Agreement clusters near 1.0, so a full 0–1 axis squashes every real
  // movement into the top quarter and the chart says nothing. The domain
  // starts at 0.5 — low enough to keep the 0.80 floor in view with context —
  // and extends downward if a measurement goes below it, so the axis never
  // truncates data, only empty space. Position encodes value here (a line, not
  // a bar), and the axis is labelled, which is what makes that honest.
  const values = usable.map((p) => p.mechanism_f1);
  const lo = Math.max(0, Math.min(0.5, Math.min(...values) - 0.05));
  const ticks = [lo, lo + (1 - lo) / 2, 1];

  const xs = (i) => pad.left + (i * (w - pad.left - pad.right)) / (usable.length - 1);
  const ys = (v) => pad.top + (1 - (v - lo) / (1 - lo)) * (h - pad.top - pad.bottom);
  const line = usable.map((p, i) => `${i ? "L" : "M"}${xs(i)},${ys(p.mechanism_f1)}`).join(" ");

  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-2)", padding: 12, overflowX: "auto" }}>
      <svg width={w} height={h} role="img" aria-label={`Classifier agreement over ${usable.length} measurements, floor ${floor}`}>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={pad.left} x2={w - pad.right} y1={ys(v)} y2={ys(v)} stroke="#333331" strokeWidth="1" />
            <text x={4} y={ys(v) + 4} fill="#6f6e69" fontSize="10">{v.toFixed(2)}</text>
          </g>
        ))}
        {/* The alert threshold, dashed and labelled so it is never mistaken for data. */}
        <line x1={pad.left} x2={w - pad.right} y1={ys(floor)} y2={ys(floor)} stroke={NEGATIVE} strokeWidth="1" strokeDasharray="4 3" />
        <text x={w - pad.right} y={ys(floor) - 5} fill={NEGATIVE} fontSize="10" textAnchor="end">alert floor {floor.toFixed(2)}</text>

        <path d={line} fill="none" stroke={ACCENT} strokeWidth="2" />
        {usable.map((p, i) => (
          <circle key={i} cx={xs(i)} cy={ys(p.mechanism_f1)} r="4"
                  fill={p.mechanism_f1 < floor ? NEGATIVE : ACCENT}
                  stroke="var(--bg-2)" strokeWidth="2">
            <title>{`${p.at ? p.at.slice(0, 16).replace("T", " ") : "—"} · F1 ${p.mechanism_f1.toFixed(3)} · ${p.compared} items`}</title>
          </circle>
        ))}
        {/* Label the ends only — a number on every point is noise. */}
        <text x={xs(usable.length - 1)} y={ys(usable[usable.length - 1].mechanism_f1) - 10} fill="#f5f4f1" fontSize="11" textAnchor="end">
          {usable[usable.length - 1].mechanism_f1.toFixed(3)}
        </text>
      </svg>
    </div>
  );
}

function OpsView() {
  const [health, setHealth] = useState(null);
  const [runs, setRuns] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [drift, setDrift] = useState([]);
  const [kind, setKind] = useState("system");
  const [error, setError] = useState(null);
  const [clearing, setClearing] = useState(false);
  // Bumped after acknowledging, to re-run the fetches below rather than
  // duplicating their URLs in the handler — two ways to build the same request
  // is two places for the `kind` filter to drift out of agreement.
  const [reload, setReload] = useState(0);

  // A 500 from FastAPI has a valid JSON body, so a bare `r.json()` *resolves*
  // and `.catch` never fires: `runs` would be set to {detail: "..."} and the
  // page would crash on `runs.map`, going blank precisely when the backend is
  // broken — the one moment anyone opens it.
  useEffect(() => {
    Promise.all([
      apiFetch("/api/health"),
      apiFetch("/api/runs?limit=12"),
      apiFetch("/api/drift"),
    ])
      .then(([h, r, d]) => { setHealth(h); setRuns(r); setDrift(d); })
      .catch((e) => setError(String(e)));
  }, [reload]);

  useEffect(() => {
    const params = new URLSearchParams({ limit: "40" });
    if (kind !== "all") params.set("kind", kind);
    apiFetch(`/api/alerts?${params}`)
      .then(setAlerts).catch((e) => setError(String(e)));
  }, [kind, reload]);

  // Acknowledge, not delete. The rows below stay exactly as they are; what this
  // resets is the header badge, which counts *unacknowledged* system alerts —
  // all of them, with no time window, so nothing goes quiet on its own.
  //
  // Safe to offer because the pipeline takes the acknowledgement back on its
  // own: anything still broken makes the rules re-emit the same episode key,
  // and dispatch un-acknowledges the row. The operator is not being asked to
  // judge what has settled.
  function clearAlerts() {
    setClearing(true);
    apiFetch("/api/alerts/acknowledge", { method: "POST" })
      .then(() => setReload((n) => n + 1))
      .catch((e) => setError(String(e)))
      .finally(() => setClearing(false));
  }

  const spend = health?.spend;
  const latest = health?.latest_run;
  const failing = health?.sources_failing || [];
  const wobbling = health?.sources_wobbling || [];
  const threshold = health?.escalation_threshold ?? 3;
  // Pre-rename key as a fallback, for the deploy window in which this bundle is
  // live against an API that has not shipped yet (see frontend/app/page.js).
  const outstanding =
    health?.unacknowledged_system_alerts ?? health?.recent_system_alerts ?? 0;

  return (
    <div className="app">
      <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
        <div style={{ height: 64, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 24px", borderBottom: "1px solid var(--border)", background: "var(--bg-2)" }}>
          <div className="serif" style={{ fontSize: 18 }}>Pipeline health</div>
          <div style={{ display: "flex", gap: 8 }}>
            <a className="btn btn-ghost" href="/digest/" style={{ padding: "8px 14px", textDecoration: "none" }}>Alerts</a>
            <a className="btn btn-ghost" href="/" style={{ padding: "8px 14px", textDecoration: "none" }}>Dashboard</a>
            <a className="btn btn-ghost" href="/register/" style={{ padding: "8px 14px", textDecoration: "none" }}>Register</a>
            <a className="btn btn-ghost" href="/pipeline/" style={{ padding: "8px 14px", textDecoration: "none" }}>Pipeline</a>
            <button className="btn btn-ghost" style={{ padding: "8px 14px" }} onClick={signOut}>Sign out</button>
          </div>
        </div>

        <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 32, maxWidth: 1100 }}>
          {error ? <div style={{ color: NEGATIVE, fontSize: 13 }}>Could not reach the API: {error}</div> : null}

          <Section title="Now">
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <Tile label="Last run" value={latest ? latest.status : "—"}
                    tone={latest ? statusColor(latest.status) : undefined}
                    sub={latest ? `${latest.kind} · ${ago(latest.started_at)}` : "no runs recorded"} />
              <Tile label="Sources failing"
                    value={health ? failing.length : "—"}
                    tone={!health ? undefined : failing.length ? NEGATIVE : ACCENT}
                    sub={!health ? "health unavailable"
                         : failing.length ? failing.map((s) => s.source_id).join(", ")
                         : wobbling.length ? `all healthy · ${wobbling.length} retrying`
                         : "all healthy"} />
              <Tile label="Spend this month"
                    value={spend ? `$${spend.month_usd.toFixed(2)}` : "—"}
                    sub={spend ? `ceiling $${spend.month_limit.toFixed(2)} · $${spend.run_limit.toFixed(2)}/run` : ""} />
              <Tile label="Corpus" value={health ? health.counts.articles : "—"}
                    sub={health ? `${health.counts.connections} connections · ${health.counts.people} people` : ""} />
            </div>
          </Section>

          <Section
            title="Classifier agreement"
            note="Mechanism micro-F1 against the adjudicated gold sample. Mechanisms gate every non-zero investment score, so this is score drift. Below the floor raises a system alert."
          >
            <DriftChart points={drift} />
          </Section>

          <Section
            title="Sources"
            note={`Cross-run state. A source is escalated after ${threshold} consecutive failed firings, not on the first.`}
          >
            <div style={{ border: "1px solid var(--border)", background: "var(--bg-2)", overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                <thead>
                  <tr style={{ color: "var(--muted-2)", textAlign: "left" }}>
                    <th style={{ padding: "10px 12px", fontWeight: 400 }}>Source</th>
                    <th style={{ padding: "10px 12px", fontWeight: 400 }}>Failures</th>
                    <th style={{ padding: "10px 12px", fontWeight: 400 }}>Last success</th>
                    <th style={{ padding: "10px 12px", fontWeight: 400 }}>Last error</th>
                  </tr>
                </thead>
                <tbody>
                  {(health?.sources || []).map((s) => (
                    <tr key={`${s.leg}/${s.source_id}`} style={{ borderTop: "1px solid var(--border-soft)" }}>
                      <td style={{ padding: "10px 12px" }}>
                        <span style={{ color: "var(--muted-2)" }}>{s.leg}/</span>{s.source_id}
                        {s.disabled ? <span style={{ marginLeft: 8, color: MUTED }}>disabled</span> : null}
                      </td>
                      <td style={{ padding: "10px 12px", color: s.consecutive_failures ? NEGATIVE : "var(--muted-2)" }}>
                        {s.consecutive_failures}
                      </td>
                      <td style={{ padding: "10px 12px", color: "var(--muted)" }}>{ago(s.last_success_at)}</td>
                      <td style={{ padding: "10px 12px", color: "var(--muted-2)", maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {s.last_error || "—"}
                      </td>
                    </tr>
                  ))}
                  {!health?.sources?.length ? (
                    <tr><td colSpan={4} style={{ padding: "14px 12px", color: "var(--muted-2)" }}>No sources have run yet.</td></tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </Section>

          <Section
            title="Runs"
            note="A firing that completed with a dead source is still succeeded — the source is escalated separately. `sources failed` is where that shows."
          >
            <div style={{ display: "flex", flexDirection: "column" }}>
              {runs.map((r) => (
                <div key={r.id} style={{ borderTop: "1px solid var(--border-soft)", padding: "10px 0", display: "flex", gap: 16, alignItems: "baseline", fontSize: 12 }}>
                  <span style={{ width: 8, height: 8, background: statusColor(r.status), display: "inline-block", flex: "0 0 auto" }} />
                  <span style={{ width: 40, color: "var(--muted-2)" }}>#{r.id}</span>
                  <span style={{ width: 80 }}>{r.kind}</span>
                  <span style={{ width: 90, color: statusColor(r.status) }}>{r.status}</span>
                  <span style={{ width: 90, color: "var(--muted)" }}>{ago(r.started_at)}</span>
                  <span style={{ width: 70, color: "var(--muted)" }}>${(r.cost_usd || 0).toFixed(2)}</span>
                  <span style={{ color: r.sources_failed ? NEGATIVE : "var(--muted-2)" }}>
                    {r.sources.length ? `${r.sources.length - r.sources_failed}/${r.sources.length} sources` : "—"}
                  </span>
                  {r.error ? <span style={{ color: NEGATIVE }}>{r.error.slice(0, 80)}</span> : null}
                </div>
              ))}
              {!runs.length ? <div style={{ fontSize: 12, color: "var(--muted-2)" }}>No runs recorded.</div> : null}
            </div>
          </Section>

          <Section
            title="Alerts"
            note="Everything raised is recorded; delivery is capped per firing, so a suppressed alert is deliberate rather than failed. The header badge counts unacknowledged system alerts. Clearing acknowledges them without removing them from this list, and anything still broken un-acknowledges itself on the next firing."
          >
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {["system", "content", "all"].map((k) => (
                <button key={k} className="btn btn-ghost"
                        style={{ padding: "6px 12px", fontSize: 12, borderColor: kind === k ? ACCENT : undefined, color: kind === k ? ACCENT : undefined }}
                        onClick={() => setKind(k)}>
                  {k}
                </button>
              ))}
              <div style={{ flex: 1 }} />
              <span style={{ fontSize: 12, color: outstanding ? NEGATIVE : "var(--muted-2)" }}>
                {outstanding ? `${outstanding} outstanding` : "badge clear"}
              </span>
              <button className="btn btn-ghost"
                      disabled={clearing || !outstanding}
                      style={{ padding: "6px 12px", fontSize: 12, opacity: clearing || !outstanding ? 0.4 : 1 }}
                      onClick={clearAlerts}>
                {clearing ? "Clearing…" : "Clear health status"}
              </button>
            </div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              {alerts.map((a) => (
                <div key={a.id} style={{ borderTop: "1px solid var(--border-soft)", padding: "10px 0", display: "flex", flexDirection: "column", gap: 4 }}>
                  <div style={{ display: "flex", gap: 12, alignItems: "baseline", fontSize: 12 }}>
                    <span style={{ color: a.severity === "critical" ? NEGATIVE : a.severity === "warning" ? "#e0a458" : MUTED, width: 64 }}>
                      {a.severity}
                    </span>
                    <span style={{ color: "var(--muted-2)", width: 120 }}>{a.rule}</span>
                    <span style={{ flex: 1 }}>{a.subject}</span>
                    <span style={{ color: "var(--muted-2)" }}>
                      {a.sent_at ? "delivered" : a.delivery_error?.startsWith("suppressed") ? "suppressed" : "not sent"}
                    </span>
                    {a.acknowledged_at ? <span style={{ color: MUTED, width: 80 }}>acknowledged</span> : null}
                  </div>
                </div>
              ))}
              {!alerts.length ? <div style={{ fontSize: 12, color: "var(--muted-2)" }}>No alerts of this kind.</div> : null}
            </div>
          </Section>
        </div>
      </div>
    </div>
  );
}


export default function Ops() {
  return (
    <Gate>
      <OpsView />
    </Gate>
  );
}
