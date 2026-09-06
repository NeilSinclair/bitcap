"use client";

// Pipeline control: pick what to run, run it, watch it finish.
//
// The run itself is a background thread on the API (api/pipeline.py) and takes
// 9 to 30 minutes, so nothing here waits on a request. Starting a run returns a
// run id immediately and this page polls for state. That also means the page is
// disposable: closing the tab does not stop the run, and reopening it picks the
// run back up, because every bit of state lives in `pipeline_runs` rather than
// in this component.

import { useCallback, useEffect, useRef, useState } from "react";

import { Gate, apiFetch, signOut } from "../auth";

const ACCENT = "#5ac3f0";
const NEGATIVE = "#f2545b";
const POLL_MS = 3000;

// What each phase marker on the run row means, in words. The pipeline writes
// `stats.phase` as it goes (app/pipeline/worker.py) precisely so this page can
// say something more useful than "Running…" for eight minutes.
const PHASE_LABEL = {
  ingest: "Fetching sources",
  landing: "Storing what was fetched",
  classify: "Classifying new articles",
  drift: "Re-scoring the gold set",
  etl: "Rebuilding scores and connections",
};

// Display names. A leg missing from here falls back to its raw id, which is
// lowercase and reads as a bug next to the others -- `releases` and `posts`
// shipped that way. Kept in step with LEGS by `tests/test_api_pipeline.py`.
const LEG_LABEL = {
  announcements: "Announcements",
  papers: "Papers",
  github: "GitHub",
  releases: "Releases",
  posts: "Posts",
  drift: "Drift check",
};

function duration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const mins = Math.floor(seconds / 60);
  return `${mins}m ${String(Math.round(seconds % 60)).padStart(2, "0")}s`;
}

function clockTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function statusColor(status) {
  if (status === "succeeded") return ACCENT;
  if (status === "failed") return NEGATIVE;
  if (status === "running") return "#e8c468";
  return "var(--muted-2)";
}

function Panel({ title, note, children }) {
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

function LegRow({ leg, checked, disabled, onToggle }) {
  return (
    <label
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        padding: "14px 16px",
        border: "1px solid var(--border)",
        background: checked ? "var(--bg-3)" : "var(--bg-2)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.55 : 1,
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={() => onToggle(leg.id)}
        style={{ marginTop: 3, accentColor: ACCENT }}
      />
      <div style={{ display: "flex", flexDirection: "column", gap: 3, flex: 1 }}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
          <span style={{ fontSize: 14, fontWeight: 500 }}>{LEG_LABEL[leg.id] || leg.id}</span>
          {leg.sources > 0 ? (
            <span style={{ fontSize: 11, color: "var(--muted-2)" }}>
              {leg.enabled_sources}/{leg.sources} sources
            </span>
          ) : null}
        </div>
        <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5 }}>{leg.note}</div>
      </div>
    </label>
  );
}

function SourceTable({ sources }) {
  if (!sources.length) return null;
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-2)" }}>
      {sources.map((s, i) => (
        <div
          key={`${s.leg}-${s.source_id}-${i}`}
          style={{
            display: "grid",
            gridTemplateColumns: "110px 1fr 80px 70px 60px",
            gap: 12,
            padding: "8px 14px",
            fontSize: 12,
            borderTop: i ? "1px solid var(--border-soft)" : "none",
            alignItems: "center",
          }}
        >
          <span style={{ color: "var(--muted-2)" }}>{s.leg}</span>
          <span>{s.source_id}</span>
          <span style={{ color: statusColor(s.status) }}>{s.status}</span>
          <span style={{ color: "var(--muted)", textAlign: "right" }}>{s.items_seen} items</span>
          <span style={{ color: "var(--muted-2)", textAlign: "right" }}>
            {duration(s.duration_s)}
          </span>
        </div>
      ))}
    </div>
  );
}

function RunCard({ run, sources }) {
  const isRunning = run.status === "running";
  const progress = run.stats?.progress;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div
        style={{
          border: `1px solid ${isRunning ? "#e8c468" : "var(--border)"}`,
          background: "var(--bg-2)",
          padding: "18px 20px",
          display: "flex",
          flexDirection: "column",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {isRunning ? (
            <span
              style={{
                width: 9,
                height: 9,
                borderRadius: "50%",
                background: "#e8c468",
                animation: "bitcapPulse 1.2s ease-in-out infinite",
              }}
            />
          ) : null}
          <span
            className="serif"
            style={{ fontSize: 20, fontWeight: 500, color: statusColor(run.status) }}
          >
            {isRunning ? "Running…" : run.status === "succeeded" ? "Done" : "Failed"}
          </span>
          <span style={{ fontSize: 12, color: "var(--muted-2)" }}>
            run {run.id} · {run.kind}
          </span>
        </div>

        {isRunning ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ fontSize: 13 }}>
              {PHASE_LABEL[run.stats?.phase] || "Starting up"}
              {progress ? (
                <span style={{ color: "var(--muted)" }}>
                  {" "}— {progress.done} of {progress.total}
                </span>
              ) : null}
            </div>
            {progress ? (
              <div style={{ height: 4, background: "var(--bg-3)", border: "1px solid var(--border)" }}>
                <div
                  style={{
                    height: "100%",
                    width: `${Math.round((progress.done / progress.total) * 100)}%`,
                    background: ACCENT,
                    transition: "width 400ms linear",
                  }}
                />
              </div>
            ) : null}
            <div style={{ fontSize: 11, color: "var(--muted-2)", lineHeight: 1.5 }}>
              {run.stats?.phase === "drift"
                ? "The drift check calls the model once per gold article, deliberately uncached. The first call warms the prompt cache on its own; the rest run in parallel."
                : "Each leg is fetched in turn; sources appear below as they finish."}
            </div>
          </div>
        ) : null}

        <div style={{ display: "flex", gap: 28, flexWrap: "wrap", fontSize: 12 }}>
          <div>
            <div className="label-bracket">Started</div>
            <div style={{ marginTop: 3 }}>{clockTime(run.started_at)}</div>
          </div>
          <div>
            <div className="label-bracket">{isRunning ? "Elapsed" : "Took"}</div>
            <div style={{ marginTop: 3 }}>{duration(run.duration_s)}</div>
          </div>
          {!isRunning ? (
            <div>
              <div className="label-bracket">Finished</div>
              <div style={{ marginTop: 3, color: ACCENT }}>{clockTime(run.finished_at)}</div>
            </div>
          ) : null}
          <div>
            <div className="label-bracket">Cost</div>
            <div style={{ marginTop: 3 }}>${(run.cost_usd ?? 0).toFixed(4)}</div>
          </div>
        </div>

        {run.error ? (
          <pre
            style={{
              margin: 0,
              padding: 12,
              background: "var(--bg-3)",
              border: `1px solid ${NEGATIVE}`,
              color: NEGATIVE,
              fontSize: 11,
              lineHeight: 1.5,
              overflowX: "auto",
              whiteSpace: "pre-wrap",
            }}
          >
            {run.error}
          </pre>
        ) : null}
      </div>

      <SourceTable sources={sources} />
    </div>
  );
}

function PipelineView() {
  const [legs, setLegs] = useState([]);
  const [selected, setSelected] = useState(new Set(["announcements"]));
  const [dryRun, setDryRun] = useState(false);
  const [run, setRun] = useState(null);
  const [sources, setSources] = useState([]);
  const [error, setError] = useState(null);
  const [starting, setStarting] = useState(false);
  const timer = useRef(null);

  const isRunning = run?.status === "running";

  const poll = useCallback(async () => {
    try {
      const body = await apiFetch("/api/pipeline/current");
      setRun(body.run);
      setSources(body.sources || []);
    } catch (e) {
      setError(String(e.message || e));
    }
  }, []);

  useEffect(() => {
    apiFetch("/api/pipeline/legs").then(setLegs).catch((e) => setError(String(e.message || e)));
    poll();
  }, [poll]);

  // Poll only while something is in flight. A finished run does not change, and
  // a page left open overnight should not keep a request every three seconds
  // going against the API for no reason.
  useEffect(() => {
    if (!isRunning) {
      if (timer.current) clearInterval(timer.current);
      return undefined;
    }
    timer.current = setInterval(poll, POLL_MS);
    return () => clearInterval(timer.current);
  }, [isRunning, poll]);

  function toggle(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function start() {
    setStarting(true);
    setError(null);
    try {
      await apiFetch("/api/pipeline/run", {
        method: "POST",
        body: JSON.stringify({
          legs: [...selected].filter((id) => id !== "drift"),
          drift: selected.has("drift"),
          dry_run: dryRun,
        }),
      });
      await poll();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setStarting(false);
    }
  }

  const nothingSelected = selected.size === 0;

  return (
    <div className="app">
      <style>{`@keyframes bitcapPulse { 0%,100% { opacity: 1 } 50% { opacity: 0.25 } }`}</style>
      <div style={{ maxWidth: 900, margin: "0 auto", padding: "32px 24px 64px", display: "flex", flexDirection: "column", gap: 32 }}>
        <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div className="label-bracket">BIT Capital · internal</div>
            <div className="serif" style={{ fontSize: 24, fontWeight: 500 }}>Pipeline</div>
          </div>
          <nav style={{ display: "flex", gap: 8 }}>
            <a className="btn btn-ghost" href="/digest/" style={{ padding: "8px 14px", textDecoration: "none" }}>Alerts</a>
            <a className="btn btn-ghost" href="/" style={{ padding: "8px 14px", textDecoration: "none" }}>Dashboard</a>
            <a className="btn btn-ghost" href="/ops/" style={{ padding: "8px 14px", textDecoration: "none" }}>Health</a>
            <button className="btn btn-ghost" style={{ padding: "8px 14px" }} onClick={signOut}>Sign out</button>
          </nav>
        </header>

        {error ? (
          <div style={{ border: `1px solid ${NEGATIVE}`, color: NEGATIVE, padding: "12px 16px", fontSize: 13 }}>
            {error}
          </div>
        ) : null}

        <Panel
          title="What to run"
          note="Each leg is independent — a source that fails is recorded, not fatal to the rest."
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {legs.map((leg) => (
              <LegRow
                key={leg.id}
                leg={leg}
                checked={selected.has(leg.id)}
                disabled={isRunning}
                onToggle={toggle}
              />
            ))}
          </div>
        </Panel>

        <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <button
            className="btn"
            onClick={start}
            disabled={isRunning || starting || nothingSelected}
            style={{
              padding: "12px 28px",
              background: isRunning || nothingSelected ? "var(--bg-3)" : ACCENT,
              color: isRunning || nothingSelected ? "var(--muted-2)" : "#0d0d0d",
              borderColor: isRunning || nothingSelected ? "var(--border)" : ACCENT,
              cursor: isRunning || nothingSelected ? "not-allowed" : "pointer",
            }}
          >
            {isRunning ? "Run in progress" : starting ? "Starting…" : "Run pipeline"}
          </button>

          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--muted)" }}>
            <input
              type="checkbox"
              checked={dryRun}
              disabled={isRunning}
              onChange={() => setDryRun((v) => !v)}
              style={{ accentColor: ACCENT }}
            />
            Dry run — exercise every phase, spend nothing
          </label>
        </div>

        {run ? (
          <Panel
            title="This run"
            note={
              isRunning
                ? "Updates every few seconds. Closing this tab will not stop it."
                : "The last run recorded, whether started here or by the nightly cron."
            }
          >
            <RunCard run={run} sources={sources} />
          </Panel>
        ) : null}
      </div>
    </div>
  );
}

export default function PipelinePage() {
  return (
    <Gate>
      <PipelineView />
    </Gate>
  );
}
