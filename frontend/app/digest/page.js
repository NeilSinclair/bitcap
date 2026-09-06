"use client";

// The digest: one dated cut of the corpus, per audience.
//
// A separate route from the dashboard on purpose. The dashboard is the corpus —
// every classified article, filterable, for someone going looking. The digest is
// the opposite claim: that a handful of things mattered this window and the rest
// did not. Putting them on one page would make the cut look like another filter.
//
// The suppressed count is rendered as prominently as the items. It is the only
// thing on the page that says the taste is real: a digest that cannot state what
// it discarded is just a shorter list.

import { useEffect, useMemo, useState } from "react";

import { Gate, apiFetch, signOut } from "../auth";
// The same record the dashboard opens, not a second rendering of it. A digest
// card is a summary by design; the reader who wants the whole thing should not
// have to leave the page they live in to get it.
import { DetailPanel, decorateItems, groupAnchors, relatedByGroup } from "../detail";

const ACCENT = "#5ac3f0";
const NEGATIVE = "#f2545b";
const MUTED = "#9a9992";

function signColor(sign) {
  return sign === "positive" ? ACCENT : sign === "negative" ? NEGATIVE : MUTED;
}
function signArrow(sign) {
  return sign === "positive" ? "↑" : sign === "negative" ? "↓" : "↔";
}
function bandStyle(band) {
  if (band === "high") return { background: ACCENT, color: "#0d0d0d", borderColor: ACCENT, fontWeight: 600 };
  if (band === "medium") return { background: "transparent", color: ACCENT, borderColor: ACCENT };
  return { background: "transparent", color: MUTED, borderColor: "#333331" };
}
function actionStyle(action) {
  if (action === "adopt") return { background: ACCENT, color: "#0d0d0d", borderColor: ACCENT, fontWeight: 600 };
  return { background: "transparent", color: ACCENT, borderColor: ACCENT };
}

function Pill({ children, style }) {
  return (
    <span style={{ fontSize: 10, letterSpacing: "0.06em", textTransform: "uppercase", border: "1px solid", padding: "2px 7px", whiteSpace: "nowrap", ...style }}>
      {children}
    </span>
  );
}

// Rendered in UTC, not the reader's zone. Window boundaries are UTC and
// `published_on` is a bare date compared at date resolution (app/digest.py), so
// formatting locally shifts the label off the days the digest actually selected
// — west of Greenwich a 4–5 Sep edition renders "3 Sep — 4 Sep", and a digest is
// a dated claim. The offset is invisible in CET, which is where it was written.
function day(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric", month: "short", timeZone: "UTC",
  });
}

// The first day the window actually covers, which is not `window_start`.
// Selection is half-open at date resolution — `start.date() < published_on <=
// end.date()` in app/digest.py — so `window_start` is an *exclusive* bound and
// rendering it raw advertised a day the digest had excluded: a 48-hour window
// read as "3 Sep — 5 Sep", three days to anyone counting, while an article
// published on the 3rd was neither surfaced nor in `considered`. Shifting by a
// day makes the label the range the numbers below it describe. Correct for
// published editions too: the backend rule is the same for both.
function firstCoveredDay(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  d.setUTCDate(d.getUTCDate() + 1);   // UTC throughout, for the reason on `day`
  return d.toLocaleDateString(undefined, {
    day: "numeric", month: "short", timeZone: "UTC",
  });
}

// The cut, stated. Considered / surfaced / suppressed, with the ratio spelled
// out in words because "76" on its own reads as a failure rather than the point.
function TheCut({ stats, windowStart, windowEnd }) {
  if (!stats) return null;
  const { considered = 0, surfaced = 0, suppressed = 0 } = stats;
  return (
    <div style={{ border: "1px solid var(--border)", background: "var(--bg-2)", padding: "16px 18px", display: "flex", flexWrap: "wrap", gap: 28, alignItems: "baseline" }}>
      <div>
        <span className="label-bracket">Window</span>
        <div className="serif" style={{ fontSize: 18, marginTop: 4 }}>
          {firstCoveredDay(windowStart)} — {day(windowEnd)}
        </div>
      </div>
      <div>
        <span className="label-bracket">Considered</span>
        <div className="serif" style={{ fontSize: 18, marginTop: 4 }}>{considered}</div>
      </div>
      <div>
        <span className="label-bracket">Surfaced</span>
        <div className="serif" style={{ fontSize: 18, marginTop: 4, color: ACCENT }}>{surfaced}</div>
      </div>
      <div>
        <span className="label-bracket">Suppressed</span>
        <div className="serif" style={{ fontSize: 18, marginTop: 4 }}>{suppressed}</div>
      </div>
      <div style={{ flex: "1 1 240px", fontSize: 12, color: "var(--muted-2)", lineHeight: 1.6 }}>
        {considered === 0
          ? "Nothing was published in this window."
          : `${suppressed} of ${considered} items in this window did not clear the bar. Thresholds are in config/digest.yaml.`}
      </div>
    </div>
  );
}

// One event. Never one connection: an article that fires against fourteen
// holdings is one thing that happened, and rendering it fourteen times is the
// noise this page exists to remove.
// The `card` class is what makes a card look clickable: it carries
// `cursor: pointer`, a transition and the hover highlight the dashboard's rows
// have had all along. Wiring the click without it (D72) produced a card that
// opened when clicked and gave no sign it would — reported, fairly, as "nothing
// happens when I mouse over them, like the cards do on the dashboard".
//
// Applied ONLY when `onOpen` is set. The class promises a click unconditionally,
// so putting it on a card whose document has left the corpus would be the
// opposite mistake.
function InvestmentItem({ item, onOpen }) {
  const h = item.holdings || { named: [], more: 0, total: 0 };
  return (
    <article
      className={onOpen ? "card" : undefined}
      onClick={onOpen || undefined}
      style={{
        border: "1px solid var(--border)", background: "var(--bg-2)",
        padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12,
      }}
    >
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <Pill style={bandStyle(item.band)}>{item.band} · {Math.round(item.score)}</Pill>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>{item.lab}</span>
        <span style={{ fontSize: 12, color: "var(--muted-2)" }}>{item.date}</span>
        <span style={{ fontSize: 12, color: "var(--muted-2)" }}>{item.eventType?.replace(/_/g, " ")}</span>
      </div>

      <h3 className="serif" style={{ fontSize: 19, lineHeight: 1.3, margin: 0 }}>{item.title}</h3>
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.65, color: "var(--muted)" }}>{item.summary}</p>

      {item.mechanism ? (
        <div style={{ borderLeft: "2px solid var(--border)", paddingLeft: 14, display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ fontSize: 12 }}>
            <span style={{ color: signColor(item.mechanism.sign) }}>{signArrow(item.mechanism.sign)} {item.mechanism.label}</span>
            <span style={{ color: "var(--muted-2)" }}> · {item.mechanism.magnitude} magnitude · {item.mechanism.confidence} confidence</span>
          </div>
          <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6 }}>{item.mechanism.reason}</div>
          {/* The verbatim sentence, always. An insight without the line it came
              from is an assertion, and this page is the one a PM would act on. */}
          <blockquote className="serif" style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "var(--text)", fontStyle: "italic" }}>
            “{item.mechanism.quote}”
          </blockquote>
        </div>
      ) : null}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <span className="label-bracket">
          {h.total === 0 ? "No position touched yet" : h.total === 1 ? "1 position" : `${h.total} positions`}
        </span>
        {h.named.map((x) => (
          <span key={x.isin} title={x.why} style={{ fontSize: 12, border: "1px solid var(--border)", padding: "3px 9px", color: signColor(x.direction) }}>
            {signArrow(x.direction)} {x.holding} <span style={{ color: "var(--muted-2)" }}>{x.strength.toFixed(2)}</span>
          </span>
        ))}
        {h.more > 0 ? (
          <span style={{ fontSize: 12, color: "var(--muted-2)" }}>+{h.more} more</span>
        ) : null}
        {/* Links that exist but did not clear the strength bar. Counted, never
            named: naming them would be the digest asserting what it just
            declined to assert. */}
        {item.belowThreshold > 0 ? (
          <span style={{ fontSize: 11, color: "var(--muted-2)" }}>
            {item.belowThreshold} weaker link{item.belowThreshold === 1 ? "" : "s"} below the bar
          </span>
        ) : null}
      </div>

      <a
        href={item.sourceUrl} target="_blank" rel="noreferrer"
        onClick={(e) => e.stopPropagation()}
        style={{ fontSize: 12, color: ACCENT, alignSelf: "flex-start" }}
      >
        Primary source ↗
      </a>
    </article>
  );
}

// The `card` class is what makes a card look clickable: it carries
// `cursor: pointer`, a transition and the hover highlight the dashboard's rows
// have had all along. Wiring the click without it (D72) produced a card that
// opened when clicked and gave no sign it would — reported, fairly, as "nothing
// happens when I mouse over them, like the cards do on the dashboard".
//
// Applied ONLY when `onOpen` is set. The class promises a click unconditionally,
// so putting it on a card whose document has left the corpus would be the
// opposite mistake.
function AiItem({ item, onOpen }) {
  return (
    <article
      className={onOpen ? "card" : undefined}
      onClick={onOpen || undefined}
      style={{
        border: "1px solid var(--border)", background: "var(--bg-2)",
        padding: "18px 20px", display: "flex", flexDirection: "column", gap: 12,
      }}
    >
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <Pill style={bandStyle(item.band)}>{item.band} · {Math.round(item.score)}</Pill>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>{item.lab}</span>
        <span style={{ fontSize: 12, color: "var(--muted-2)" }}>{item.date}</span>
      </div>

      <h3 className="serif" style={{ fontSize: 19, lineHeight: 1.3, margin: 0 }}>{item.title}</h3>
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.65, color: "var(--muted)" }}>{item.summary}</p>

      {(item.practices || []).map((p, i) => (
        <div key={i} style={{ borderLeft: "2px solid var(--border)", paddingLeft: 14, display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <Pill style={actionStyle(p.action)}>{p.action}</Pill>
            <span style={{ fontSize: 12 }}>{p.label}</span>
            <span style={{ fontSize: 12, color: "var(--muted-2)" }}>{p.impact} impact · {p.confidence} confidence</span>
          </div>
          <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6 }}>{p.reason}</div>
          <blockquote className="serif" style={{ margin: 0, fontSize: 13, lineHeight: 1.6, fontStyle: "italic" }}>
            “{p.quote}”
          </blockquote>
        </div>
      ))}

      <a
        href={item.sourceUrl} target="_blank" rel="noreferrer"
        onClick={(e) => e.stopPropagation()}
        style={{ fontSize: 12, color: ACCENT, alignSelf: "flex-start" }}
      >
        Primary source ↗
      </a>
    </article>
  );
}

function DigestView() {
  const [kind, setKind] = useState("investment");
  // "current" previews the window as it stands right now; an id reads a
  // published edition back verbatim. The distinction matters — a published
  // digest is what the product said at the time, not what it would say today.
  const [editionId, setEditionId] = useState("current");
  const [preview, setPreview] = useState(null);
  const [past, setPast] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // The whole corpus, for the detail panel. Fetched once and independently of
  // the digest itself: a published edition stores only what the card shows, so
  // the full record for an item has to come from `/api/items` either way. Kept
  // out of the `[kind]` effect because it does not vary by audience — only the
  // decoration does — and out of the loading gate because a digest that renders
  // without its panel data is still a digest.
  const [corpus, setCorpus] = useState([]);
  // Why the cards cannot be opened, when they cannot. Null while loading and
  // once loaded; a string only when the fetch above actually failed, so it can
  // never be confused with "loaded, but this document is gone".
  const [corpusError, setCorpusError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      apiFetch(`/api/digests/preview?kind=${kind}`),
      apiFetch(`/api/digests?kind=${kind}&limit=20`),
    ])
      .then(([p, h]) => {
        setPreview(p);
        setPast(h);
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, [kind]);

  useEffect(() => {
    // Failure-tolerant, but NOT silent, and the first version was silent.
    //
    // `.catch(() => setCorpus([]))` swallowed everything. When this fetch fails
    // — an expired session, a restarted API, a 500 — the corpus is empty, so
    // `resolve` returns null for every card, every card loses its opener, and
    // the page renders perfectly while nothing on it can be clicked. There is no
    // error, no console line, and no visible difference from "these documents
    // have left the corpus". That is the exact shape of silent degradation this
    // project exists to avoid, and it reads to a user as the feature having been
    // broken by whatever changed most recently.
    //
    // Still tolerant: a digest that cannot open its panels is worth more than an
    // error page, so the failure is recorded rather than raised. The banner
    // below says which of the two states the reader is in.
    apiFetch("/api/items")
      .then((rows) => { setCorpus(rows); setCorpusError(null); })
      .catch((e) => { setCorpus([]); setCorpusError(String(e)); });
  }, []);

  // `kind` and the dashboard's `audience` are the same two values, so the
  // decoration is the identical call — which is the point: the panel leads with
  // whichever axis this edition is for.
  const decorated = useMemo(() => decorateItems(corpus, kind), [corpus, kind]);
  const [anchorFor] = useMemo(() => groupAnchors(decorated, kind), [decorated, kind]);
  const relatedForGroup = useMemo(
    () => relatedByGroup(decorated, anchorFor), [decorated, anchorFor]);
  // RESOLVED BY URL, WITH THE ID ONLY AS A FALLBACK, and the order is the point.
  // docs/decisions.md D72.
  //
  // `articles.id` is a surrogate autoincrement key. Every rebuild reassigns it,
  // and a published payload is frozen at the moment it was written — so a stored
  // id goes stale the first time anyone rebuilds, which on this project is
  // constantly. `articles.url` is unique (746 of 746 on the live corpus) and is
  // what the document actually *is*.
  //
  // Measured across all 30 published editions, 65 items:
  //
  //     resolvable by id :  9  (13%)
  //     resolvable by url: 61  (93%)
  //
  // Editions 99 and 100 are the sharpest case: published hours ago, both already
  // resolve zero items by id and all of theirs by url. Matching on id first
  // would have made the archive almost entirely dead while looking deliberate.
  //
  // The id fallback is allowed ONLY on the live preview, and that restriction is
  // the whole safety argument. A preview is built by the process now serving the
  // corpus, so its ids cannot be stale. An archived payload's ids can not only
  // be stale but *recycled* — the counter is reused, so id 4454 today may be a
  // different document than the one the card names. Falling back there would
  // open the wrong article while looking like it worked, which is strictly worse
  // than the dead card this commit set out to fix.
  const [byUrl, byId] = useMemo(() => {
    const urls = {};
    const ids = {};
    for (const it of decorated) {
      urls[it.sourceUrl] = it;
      ids[it.id] = it;
    }
    return [urls, ids];
  }, [decorated]);

  // What a card resolves to, or null if the document has genuinely left the
  // corpus — 4 of the 65 published items, which stay unclickable, correctly.
  const isLiveEdition = editionId === "current";
  const resolve = (item) =>
    byUrl[item.sourceUrl] || (isLiveEdition ? byId[item.id] : null) || null;

  // `selectedId` is whatever `resolve` returned an id for, so it is always a
  // live id from the current corpus rather than one read out of a payload.
  const selected = selectedId ? byId[selectedId] : null;

  const edition =
    editionId === "current"
      ? preview
      : past.find((d) => String(d.id) === String(editionId));

  const items = edition?.items || [];
  const stats = edition?.stats;
  const windowStart = edition?.window_start || edition?.windowStart;
  const windowEnd = edition?.window_end || edition?.windowEnd;

  return (
    <div className="app">
      <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
        <div style={{ height: 64, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 24px", borderBottom: "1px solid var(--border)", background: "var(--bg-2)" }}>
          <div className="serif" style={{ fontSize: 18 }}>Alerts</div>
          <div style={{ display: "flex", gap: 8 }}>
            <a className="btn btn-ghost" href="/" style={{ padding: "8px 14px", textDecoration: "none" }}>Dashboard</a>
            <a className="btn btn-ghost" href="/register/" style={{ padding: "8px 14px", textDecoration: "none" }}>Register</a>
            <a className="btn btn-ghost" href="/pipeline/" style={{ padding: "8px 14px", textDecoration: "none" }}>Pipeline</a>
            <a className="btn btn-ghost" href="/ops/" style={{ padding: "8px 14px", textDecoration: "none" }}>Health</a>
            <button className="btn btn-ghost" style={{ padding: "8px 14px" }} onClick={signOut}>Sign out</button>
          </div>
        </div>

        <div style={{ padding: 24, display: "flex", flexDirection: "column", gap: 24, maxWidth: 900 }}>
          {error ? <div style={{ color: NEGATIVE, fontSize: 13 }}>Could not reach the API: {error}</div> : null}

          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center" }}>
            <div style={{ display: "flex", gap: 0 }}>
              {[["investment", "Investment team"], ["ai", "AI team"]].map(([k, label]) => (
                <button
                  key={k}
                  className="btn btn-ghost"
                  onClick={() => { setKind(k); setEditionId("current"); }}
                  style={{ padding: "8px 16px", borderColor: kind === k ? ACCENT : undefined, color: kind === k ? ACCENT : undefined }}
                >
                  {label}
                </button>
              ))}
            </div>

            <select
              className="field-input"
              value={editionId}
              onChange={(e) => setEditionId(e.target.value)}
              style={{ padding: "8px 12px", fontSize: 13 }}
            >
              <option value="current">Current window (unpublished)</option>
              {past.map((d) => (
                <option key={d.id} value={d.id}>
                  Published {day(d.windowEnd)} · {d.stats?.surfaced ?? 0} of {d.stats?.considered ?? 0}
                </option>
              ))}
            </select>
          </div>

          <TheCut stats={stats} windowStart={windowStart} windowEnd={windowEnd} />

          {/* The cards are still correct and still worth reading — only the
              click-through is gone. Saying so is the whole point: without this
              line the page looks identical to a working one, and the reader is
              left to guess whether the feature was removed. */}
          {corpusError ? (
            <div style={{ border: `1px solid ${NEGATIVE}`, background: "var(--bg-2)", padding: "12px 16px", fontSize: 12.5, color: "var(--muted)", lineHeight: 1.6 }}>
              <span style={{ color: NEGATIVE }}>Cards cannot be opened.</span>{" "}
              The corpus behind the detail panel did not load, so the items below
              still show their summary but will not expand. Everything else on
              this page is unaffected.
              <div style={{ marginTop: 6, color: "var(--muted-2)", fontSize: 11.5 }}>
                {corpusError}
              </div>
            </div>
          ) : null}

          {loading ? (
            <div style={{ fontSize: 13, color: "var(--muted-2)" }}>Loading…</div>
          ) : items.length ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              {/* `onOpen` only where the document is still in the corpus. A
                  published edition can outlive the article it names — the
                  payload is frozen, the corpus is not — and a card that looks
                  clickable and opens nothing is worse than one that does not
                  invite the click.

                  The opener is handed the id `resolve` found, NOT the id stored
                  in the payload: the whole point of matching on url is that the
                  stored id is the stale one. */}
              {items.map((item) => {
                const live = resolve(item);
                const open = live ? () => setSelectedId(live.id) : null;
                return kind === "investment"
                  ? <InvestmentItem key={item.id} item={item} onOpen={open} />
                  : <AiItem key={item.id} item={item} onOpen={open} />;
              })}
            </div>
          ) : (
            // An empty digest is a real answer, not a failure state. Saying so
            // is the difference between "the labs were quiet" and "it broke".
            <div style={{ border: "1px solid var(--border)", background: "var(--bg-2)", padding: "24px 20px", fontSize: 13, color: "var(--muted)", lineHeight: 1.7 }}>
              Nothing cleared the bar in this window.
              {stats?.considered
                ? ` All ${stats.considered} item${stats.considered === 1 ? "" : "s"} published in it were suppressed — that is the filter working, not a fault.`
                : " Nothing was published in it."}
            </div>
          )}
        </div>
      </div>

      {/* Wrapped in a fixed, viewport-sized box because the panel positions
          itself absolutely and this page — unlike the dashboard — is as tall as
          its content. Anchored to `.app` directly, `bottom: 0` would stretch the
          panel to the whole scroll height. */}
      {selected && (
        <div style={{ position: "fixed", inset: 0, zIndex: 50 }}>
          <DetailPanel
            item={selected}
            related={relatedForGroup[selected.groupId]}
            audience={kind}
            onOpen={setSelectedId}
            onClose={() => setSelectedId(null)}
          />
        </div>
      )}
    </div>
  );
}

export default function Digest() {
  return (
    <Gate>
      <DigestView />
    </Gate>
  );
}
