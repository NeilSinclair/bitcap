"use client";

// The register: who the system tracks, and the moves hiding in it.
//
// The headline is the move candidates, not the totals. The register keeps
// per-lab records and refuses to merge a name across labs, so a researcher who
// moves shows up as one name with two records — a visible anomaly rather than a
// row that got tidied away. That refusal is the design (docs/decisions.md D25),
// and this page is what it buys.
//
// The totals are shown with their evidence tiers attached, never bare. Most of
// the register is drive-by open-source contributors, so "4,941 people" on its
// own overstates what the system knows by about four to one.

import { useEffect, useMemo, useState } from "react";

import { Gate, apiFetch, signOut } from "../auth";

const ACCENT = "#5ac3f0";
const NEGATIVE = "#f2545b";
const MUTED = "#9a9992";

function Section({ title, note, children }) {
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="label-bracket">{title}</span>
        {note ? <div style={{ fontSize: 12, color: "var(--muted-2)", maxWidth: 680, lineHeight: 1.6 }}>{note}</div> : null}
      </div>
      {children}
    </section>
  );
}

function Tile({ label, value, sub }) {
  return (
    <div style={{ flex: "1 1 160px", minWidth: 160, border: "1px solid var(--border)", background: "var(--bg-2)", padding: "14px 16px", display: "flex", flexDirection: "column", gap: 6 }}>
      <span className="label-bracket">{label}</span>
      <div className="serif" style={{ fontSize: 24, lineHeight: 1.1 }}>{value}</div>
      {sub ? <div style={{ fontSize: 11, color: "var(--muted-2)" }}>{sub}</div> : null}
    </div>
  );
}

function years(days) {
  if (days === null || days === undefined) return "";
  // Negative means the two tenures overlap — published at both labs in the same
  // period, which is a different and more interesting thing than a clean move.
  if (days <= 0) return `overlapping by ${Math.abs(days)} days`;
  if (days < 400) return `${days} days apart`;
  return `${(days / 365).toFixed(1)} years apart`;
}

// One candidate. Deliberately verbose: it is a claim about a named person, so
// both sides' dates, tiers and primary sources travel with it and the wording
// never says "moved".
function Candidate({ c }) {
  return (
    <article style={{ border: "1px solid var(--border)", background: "var(--bg-2)", padding: "16px 18px", display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <h3 className="serif" style={{ fontSize: 18, margin: 0 }}>{c.name}</h3>
        <span style={{ fontSize: 13, color: ACCENT }}>{c.readsAs}</span>
        <span style={{ fontSize: 12, color: "var(--muted-2)" }}>
          {/* Never "github byline": a commit is not a claim of authorship, and
              conflating the two is what app/people.py exists to refuse. */}
          {c.sourceKind === "paper" ? "paper byline" : "commit history"}
          {c.gapDays === null || c.gapDays === undefined ? null : ` · ${years(c.gapDays)}`}
        </span>
      </div>

      <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
        {c.sides.map((s, i) => (
          <div key={i} style={{ flex: "1 1 260px", borderLeft: "2px solid var(--border)", paddingLeft: 14, display: "flex", flexDirection: "column", gap: 5 }}>
            <div style={{ fontSize: 13 }}>{s.label}</div>
            <div style={{ fontSize: 12, color: "var(--muted-2)" }}>
              {s.firstSeen === s.lastSeen ? s.lastSeen : `${s.firstSeen} → ${s.lastSeen}`}
              {" · "}
              <span style={{ color: s.assertsEmployment ? ACCENT : MUTED }}>
                {s.assertsEmployment ? "employment evidence" : `tier: ${s.tiers.join(", ")}`}
              </span>
            </div>
            {s.sources.map((u) => (
              <a key={u} href={u} target="_blank" rel="noreferrer" style={{ fontSize: 11, color: ACCENT, wordBreak: "break-all" }}>{u}</a>
            ))}
          </div>
        ))}
      </div>

      {/* The register is not entitled to call this a move, and says so. */}
      <div style={{ fontSize: 11, color: "var(--muted-2)", lineHeight: 1.6 }}>
        Candidate, not a confirmed move — same name is not same person, and a
        byline can be published long after the work moved. Both primary sources
        are linked above so this is a minute of checking, not a research task.
      </div>
    </article>
  );
}

function RegisterView() {
  const [data, setData] = useState(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    apiFetch("/api/register").then(setData).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) { setResults(null); return; }
    // Debounced: the register is 4,941 people and every keystroke is a LIKE.
    const t = setTimeout(() => {
      apiFetch(`/api/register/search?q=${encodeURIComponent(q)}`)
        .then(setResults).catch((e) => setError(String(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  const totals = data?.totals;
  const moveStats = data?.moveStats;
  const dropped = moveStats
    ? (moveStats.paper.raw - moveStats.paper.kept) + (moveStats.github.raw - moveStats.github.kept)
    : 0;
  const raw = moveStats ? moveStats.paper.raw + moveStats.github.raw : 0;

  return (
    <div className="app">
      <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
        <div style={{ height: 64, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 24px", borderBottom: "1px solid var(--border)", background: "var(--bg-2)" }}>
          <div className="serif" style={{ fontSize: 18 }}>Register</div>
          <div style={{ display: "flex", gap: 8 }}>
            <a className="btn btn-ghost" href="/" style={{ padding: "8px 14px", textDecoration: "none" }}>Dashboard</a>
            <a className="btn btn-ghost" href="/digest/" style={{ padding: "8px 14px", textDecoration: "none" }}>Digest</a>
            <a className="btn btn-ghost" href="/ops/" style={{ padding: "8px 14px", textDecoration: "none" }}>Health</a>
            <button className="btn btn-ghost" style={{ padding: "8px 14px" }} onClick={signOut}>Sign out</button>
          </div>
        </div>

        <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 32, maxWidth: 1000 }}>
          {error ? <div style={{ color: NEGATIVE, fontSize: 13 }}>Could not reach the API: {error}</div> : null}
          {!data && !error ? <div style={{ fontSize: 13, color: "var(--muted-2)" }}>Loading…</div> : null}

          {data ? (
            <>
              <Section
                title="Possible researcher moves"
                note={`A name recorded under two labs. These fall out of the register precisely because it refuses to merge people across labs — merged, each of these would be one tidy row and the signal would be gone. ${raw} cross-lab names exist; ${dropped} were dropped as outside contributors with no lab-domain evidence on either side.`}
              >
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {(data.moveCandidates || []).map((c) => <Candidate key={`${c.sourceKind}:${c.name}`} c={c} />)}
                  {!data.moveCandidates?.length ? (
                    <div style={{ fontSize: 13, color: "var(--muted-2)" }}>No cross-lab names survive the evidence gate.</div>
                  ) : null}
                </div>
              </Section>

              <Section
                title="Coverage"
                note="A raw contributor count is close to meaningless as a measure of a lab — most of the register is open-source traffic, not staff — so every figure here carries its evidence tier."
              >
                <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                  <Tile label="People" value={totals.people.toLocaleString()} sub="one record per lab, never merged" />
                  <Tile label="Employment evidenced" value={totals.employment_evidenced.toLocaleString()}
                        sub={totals.people
                          ? `${Math.round(100 * totals.employment_evidenced / totals.people)}% — a lab-owned email domain`
                          : "a lab-owned email domain"} />
                  <Tile label="Identities" value={totals.identities.toLocaleString()} sub="the entity-resolution surface" />
                  <Tile label="Evidence rows" value={totals.evidence.toLocaleString()} sub="every record is sourced" />
                </div>

                <div style={{ border: "1px solid var(--border)", background: "var(--bg-2)", overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead>
                      <tr style={{ color: "var(--muted-2)", textAlign: "left" }}>
                        <th style={{ padding: "10px 12px", fontWeight: 400 }}>Lab</th>
                        <th style={{ padding: "10px 12px", fontWeight: 400 }}>Papers</th>
                        <th style={{ padding: "10px 12px", fontWeight: 400 }}>GitHub</th>
                        <th style={{ padding: "10px 12px", fontWeight: 400 }}>Employment evidenced</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.labs.map((l) => (
                        <tr key={l.lab} style={{ borderTop: "1px solid var(--border)" }}>
                          <td style={{ padding: "10px 12px" }}>{l.label}</td>
                          <td style={{ padding: "10px 12px", color: "var(--muted)" }}>{l.paper}</td>
                          <td style={{ padding: "10px 12px", color: "var(--muted)" }}>{l.github}</td>
                          <td style={{ padding: "10px 12px", color: l.employment_evidenced ? ACCENT : MUTED }}>{l.employment_evidenced}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {data.tiers.map((t) => (
                    <span key={t.tier} style={{ fontSize: 11, border: "1px solid var(--border)", padding: "4px 9px", color: t.asserts_employment ? ACCENT : MUTED }}>
                      {t.tier} {t.count.toLocaleString()}
                    </span>
                  ))}
                </div>
              </Section>

              <Section
                title="Look someone up"
                note="Searches canonical names and every recorded identity, so a GitHub login finds the person without knowing the byline it was harvested beside."
              >
                <input
                  className="field-input"
                  placeholder="Name or GitHub login…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  style={{ padding: "10px 12px", fontSize: 13, maxWidth: 360 }}
                />
                {results ? (
                  results.length ? (
                    <div style={{ border: "1px solid var(--border)", background: "var(--bg-2)", overflowX: "auto" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                        <tbody>
                          {results.map((r) => (
                            <tr key={r.id} style={{ borderTop: "1px solid var(--border)" }}>
                              <td style={{ padding: "9px 12px" }}>{r.name}</td>
                              <td style={{ padding: "9px 12px", color: "var(--muted)" }}>{r.label}</td>
                              <td style={{ padding: "9px 12px", color: "var(--muted-2)" }}>{r.sourceKind}</td>
                              <td style={{ padding: "9px 12px", color: "var(--muted-2)" }}>{r.lastSeen || "—"}</td>
                              <td style={{ padding: "9px 12px", color: r.assertsEmployment ? ACCENT : MUTED }}>{r.tiers.join(", ")}</td>
                              <td style={{ padding: "9px 12px", color: "var(--muted-2)" }}>
                                {r.identities.map((i) => i.value).join(", ")}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div style={{ fontSize: 12, color: "var(--muted-2)" }}>No one in the register matches that.</div>
                  )
                ) : null}
              </Section>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default function Register() {
  return (
    <Gate>
      <RegisterView />
    </Gate>
  );
}
