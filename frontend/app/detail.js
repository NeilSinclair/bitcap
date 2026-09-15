"use client";

// The full record for one item, and the decoration it needs — shared by the
// dashboard and the digest.
//
// WHY THIS FILE EXISTS (docs/decisions.md D72). The dashboard grew a detail
// panel; the digest grew a
// card that summarised the same article more thinly. Two renderings of one
// record drift, and the thin one is the problem: the digest is the surface a
// reader is meant to live in, so the *less* complete view was the one they
// spent their time in. Reproducing the panel in the digest would have made two
// copies to keep in step, which is the same failure a step later.
//
// So the panel and the decoration it depends on live here, and both pages
// import them. Nothing about the panel is dashboard-specific: it takes an
// already-decorated item, its related list, and the audience.
//
// The decoration is NOT cosmetic and cannot be skipped by a caller that only
// wants "the raw item" — `DetailPanel` reads `connectionGroups`, `chipScore`,
// `actionStyleObj` and the `color`/`arrow` fields, none of which the API sends.
// `decorateItems` is the only supported way to produce its input.

import { useState } from "react";

export const ACCENT = "#5ac3f0";
export const NEGATIVE = "#f2545b";
export const MUTED = "#9a9992";

export function signColor(sign) {
  return sign === "positive" ? ACCENT : sign === "negative" ? NEGATIVE : MUTED;
}

export function signArrow(sign) {
  return sign === "positive" ? "↑" : sign === "negative" ? "↓" : "↔";
}

// THE DASHBOARD'S VARIANTS, not the digest's, and the difference is real.
// `frontend/app/digest/page.js` carries its own three-branch `bandStyle` and
// two-branch `actionStyle`, which collapse `low`/`none` and `investigate`/
// `watch` into one style each. That is fine for a digest card, where nothing
// below medium is ever rendered. The dashboard shows the whole corpus, so it
// distinguishes four bands and three actions — and importing the digest's
// versions here would silently restyle every low and unbanded row on the
// dashboard. The digest keeps its own for its cards; only the panel is shared.
export function bandStyle(band) {
  if (band === "high") return { background: ACCENT, color: "#0d0d0d", borderColor: ACCENT, fontWeight: 600 };
  if (band === "medium") return { background: "transparent", color: ACCENT, borderColor: ACCENT };
  if (band === "low") return { background: "transparent", color: MUTED, borderColor: "#333331" };
  return { background: "transparent", color: "#6f6e69", borderColor: "#333331" };
}

export function actionStyle(action) {
  if (action === "adopt") return { background: ACCENT, color: "#0d0d0d", borderColor: ACCENT, fontWeight: 600 };
  if (action === "investigate") return { background: "transparent", color: ACCENT, borderColor: ACCENT };
  return { background: "transparent", color: MUTED, borderColor: "#333331" };
}

// Related-but-distinct articles, deliberately shaped unlike a fold. A fold says
// "this is the same event, I have hidden the rest"; this says "these are
// different documents about the same model, both are in the feed". So it opens
// by default and never implies anything was removed.
export function RelatedItems({ items, onOpen }) {
  return (
    <div style={{ borderTop: "1px solid var(--line)", paddingTop: 8, marginTop: 2 }}>
      <div style={{ fontSize: 12, color: "var(--muted-2)", marginBottom: 6 }}>
        Also mentions {[...new Set(items.map((r) => r.evidence))].join(", ")}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {items.map((r) => (
          <div
            key={`${r.id}-${r.evidence}`}
            onClick={(e) => { e.stopPropagation(); onOpen(r.id); }}
            style={{ cursor: "pointer", fontSize: 12, color: "var(--muted)", lineHeight: 1.4 }}
          >
            <span className="tag-pill" style={{ color: "var(--muted-2)", marginRight: 6 }}>{r.docType}</span>
            {r.title}
          </div>
        ))}
      </div>
    </div>
  );
}

// The folded members of a near-duplicate group, collapsed behind a disclosure.
export function FoldedGroup({ members, reason, onOpen }) {
  const [open, setOpen] = useState(false);
  const span = members.length === 1 ? "1 more" : `${members.length} more`;
  return (
    <div style={{ borderTop: "1px solid var(--line)", paddingTop: 8, marginTop: 2 }}>
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        style={{
          background: "none", border: "none", padding: 0, cursor: "pointer",
          font: "inherit", fontSize: 12, color: "var(--muted-2)",
        }}
      >
        {open ? "▾" : "▸"} {span} on this — same event
      </button>
      {open && (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
          {/* Why they were merged, in the model's or the rule's own words. A
              collapse the reader cannot interrogate is a collapse they have to
              take on trust. */}
          <div style={{ fontSize: 11, color: "var(--muted-2)", fontStyle: "italic" }}>{reason}</div>
          {members.map((f) => (
            <div
              key={f.id}
              onClick={(e) => { e.stopPropagation(); onOpen(f.id); }}
              style={{ cursor: "pointer", fontSize: 12, color: "var(--muted)", lineHeight: 1.4 }}
            >
              <span style={{ color: "var(--muted-2)" }}>{f.date}</span> · {f.title}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Everything the panel and the feed read that the API does not send: colours,
// arrows, per-audience scores, and connections regrouped by company.
//
// Audience-dependent, so it must be recomputed when the tab changes rather than
// cached once — `displayScore`, `displayBand` and the pills all differ between
// the two cuts of the same article.
export function decorateItems(items, audience) {
  return items.map((it) => {
    const mechanisms = it.mechanisms.map((t) => ({ ...t, color: signColor(t.sign), arrow: signArrow(t.sign) }));
    const connections = it.connections.map((c) => ({
      ...c,
      color: signColor(c.direction),
      arrow: signArrow(c.direction),
      // `note` prefers the holding-specific "why" (app/connect.py) and
      // only falls back to the article's own reason when there isn't one.
      // Category rows never carry a holding-side why (membership has no
      // company-specific evidence), so their note would repeat the tag's own
      // reason. They are hidden by config today (D82) and the category Evidence
      // cards are gone, so re-enabling the route must bring that reason back
      // somewhere — until then a category row would show with none.
      // Every other route's note is genuinely distinct information.
      showNote: c.route !== "category",
      caption: c.magnitude && c.confidence
        ? `${c.magnitude} magnitude · ${c.confidence} confidence`
        : c.confidence
        ? `${c.confidence} confidence`
        : null,
    }));
    const practices = it.practices.map((p) => ({ ...p, actionStyleObj: actionStyle(p.action) }));

    const evidencePills = audience === "investment"
      ? mechanisms.slice(0, 2).map((t) => ({ label: t.label, color: t.color, arrow: t.arrow }))
      : practices.slice(0, 2).map((p) => ({ label: p.label, color: ACCENT, arrow: p.action === "adopt" ? "↑" : "→" }));

    const impactPills = audience === "investment"
      ? connections.slice(0, 2).map((c) => ({ label: `${c.holding} ${c.strength.toFixed(2)}`, color: c.color }))
      : [];

    // Grouped by company for the detail panel: a big article can connect
    // to a dozen holdings across several routes each, and a flat list of
    // rows reading "mechanism / −0.33" over and over is unreadable —
    // "which company, what specifically, how strong" is the layout below.
    const byHolding = new Map();
    for (const c of connections) {
      if (!byHolding.has(c.holding)) byHolding.set(c.holding, []);
      byHolding.get(c.holding).push(c);
    }
    const connectionGroups = [...byHolding.entries()]
      .map(([holding, rows]) => ({
        holding,
        rows: [...rows].sort((a, b) => b.strength - a.strength),
        maxStrength: Math.max(...rows.map((r) => r.strength)),
      }))
      .sort((a, b) => b.maxStrength - a.maxStrength);

    // Investment ranks on the higher of the event and holding scores, the other
    // breaking ties (app/ranking.py), so the chip shows that value and names
    // which score set it.
    const investment = audience === "investment";
    const holdingScore = it.holdingScore || 0;
    const leadLink = it.rankLead === "holding" && it.holdingBasis;
    const displayScore = (investment ? (it.rankValue ?? it.score) : it.aiScore).toFixed(1);
    const displayBand = investment ? (it.rankBand || it.band) : it.aiBand;

    return {
      ...it,
      mechanisms,
      connections,
      connectionGroups,
      practices,
      evidencePills,
      impactPills,
      showImpactRow: audience === "investment" && impactPills.length > 0,
      displayScore,
      displayBand,
      displayStyle: bandStyle(displayBand),
      rankMin: Math.min(it.score, holdingScore),
      displayLead: investment ? (leadLink ? leadHoldings(it.holdingBasis) : "Event") : null,
      displaySecondary: !investment ? null
        : leadLink ? `event ${it.score.toFixed(1)}`
        : holdingScore > 0 ? `holding ${holdingScore.toFixed(1)}` : null,
      chipScore: it.score.toFixed(1),
      aiChipScore: it.aiScore.toFixed(1),
    };
  });
}

// Which member of a near-duplicate group speaks for it, decided per audience
// rather than read from `isAnchor`. That flag is picked once over the whole
// corpus on a single score; event_type is a multiplicative term in the
// investment score and absent from the AI score, so the member ranking
// highest overall can score zero on the axis being displayed — and the member
// carrying the signal for this audience is the one that got folded.
export function groupAnchors(decorated, audience) {
  const investment = audience === "investment";
  // Investment: a score-0 member never speaks for a group or sits folded in
  // one, so a group of only score-0 members has no anchor and is not in the
  // feed (D81). The digest drops the same rows before grouping.
  const eligible = investment ? decorated.filter((it) => it.score > 0) : decorated;
  // Merit first: investment on the higher of event and holding score, then the
  // other (app/ranking.py); AI on its own score. Then by date — and the date
  // direction flips for release trains, where every member usually scores the
  // same so this decides every one of them. A repo's card must name the
  // version it is on, not the one it has left. Everywhere else earliest wins,
  // because being early is the claim. Same rule as app/digest.py `rank`; the
  // two must agree or the feed and the digest name different articles as the
  // same event.
  const merit = (it) => (investment
    ? [it.rankValue ?? it.score ?? 0, it.rankMin ?? 0]
    : [it.aiScore || 0, 0]);
  const better = (a, b) => {
    const [ma, mb] = [merit(a), merit(b)];
    if (ma[0] !== mb[0]) return ma[0] > mb[0];
    if (ma[1] !== mb[1]) return ma[1] > mb[1];
    const latest = a.groupMethod === "release_train";
    if (a.date !== b.date) return latest ? a.date > b.date : a.date < b.date;
    // Ids break a full tie, or the winner depends on the order the API
    // happened to return rows in — which has no secondary sort within a day.
    return latest ? a.id > b.id : a.id < b.id;
  };
  const best = {};
  for (const it of eligible) {
    const cur = best[it.groupId];
    if (!cur || better(it, cur)) best[it.groupId] = it;
  }
  const folded = {};
  for (const it of eligible) {
    if (best[it.groupId] === it) continue;
    (folded[it.groupId] = folded[it.groupId] || []).push(it);
  }
  // Investment lists folded members in merit order, so the next-best document
  // reads first; AI keeps newest first.
  const order = investment
    ? (a, b) => (better(a, b) ? -1 : better(b, a) ? 1 : 0)
    : (a, b) => b.date.localeCompare(a.date);
  for (const list of Object.values(folded)) list.sort(order);
  return [best, folded];
}

// Related items, resolved through the group anchor rather than rendered as
// stored. Two things go wrong without this, and both were found in review:
//
// * 17 of 25 links on the live corpus point at a FOLDED member, so the link
//   was written, counted, and never displayed anywhere.
// * The three GPT-6 Astra posts are one group, so a release linked to all
//   three listed one launch three times.
//
// Keyed by GROUP, not by article, so a link carried by a folded member still
// reaches the card its reader is actually looking at.
export function relatedByGroup(decorated, anchorFor) {
  const byId = {};
  for (const it of decorated) byId[it.id] = it;
  const out = {};
  for (const it of decorated) {
    const seen = out[it.groupId] || (out[it.groupId] = new Map());
    for (const rel of it.relatedTo || []) {
      const target = byId[rel.id];
      if (!target) continue;
      const anchor = anchorFor[target.groupId] || target;
      // A link inside one's own group is the grouping's story, not a
      // cross-reference — FoldedGroup already says it, and better.
      if (anchor.groupId === it.groupId) continue;
      if (!seen.has(anchor.id)) {
        seen.set(anchor.id, { ...rel, id: anchor.id, title: anchor.title, docType: anchor.docType });
      }
    }
  }
  return Object.fromEntries(
    Object.entries(out).map(([groupId, seen]) => [groupId, [...seen.values()]]));
}

// One piece of evidence behind the rank: a heading, what it is, and the reason
// and verbatim quote where it has them.
function BasisBlock({ heading, sign, label, detail, reason, quote, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, border: "1px solid var(--border)", padding: "12px 14px" }}>
      <span style={{ fontSize: 11, color: "var(--muted-2)", textTransform: "uppercase", letterSpacing: "0.06em" }}>{heading}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
        {sign && <span style={{ color: signColor(sign) }}>{signArrow(sign)}</span>}
        <span style={{ fontWeight: 600 }}>{label}</span>
        {detail && <span style={{ color: "var(--muted-2)" }}>· {detail}</span>}
      </div>
      {reason && <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>{reason}</div>}
      {quote && <div className="quote-block">&ldquo;{quote}&rdquo;</div>}
      {children}
    </div>
  );
}

function sizing(magnitude, confidence) {
  return [magnitude, confidence].filter(Boolean).join(" / ");
}

// Every holding tied at the top holding strength, named together. One tag at
// 1.00 on Amazon, Micron and NVIDIA is news for all three; naming only the
// first made it read as Amazon's alone. Past three, the rest are counted.
export function leadHoldings(basis) {
  const names = [basis.holding, ...(basis.tiedWith || []).map((t) => t.holding)];
  return names.length > 3 ? `${names.slice(0, 3).join(", ")} +${names.length - 3}` : names.join(", ");
}

// A holding edge's source, when config/companies.yaml records one.
function SourceLine({ source }) {
  if (!source) return null;
  if (/^https?:\/\//.test(source)) {
    return <a href={source} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: ACCENT }}>Source ↗</a>;
  }
  return <div style={{ fontSize: 12, color: "var(--muted-2)" }}>Source: {source}</div>;
}

// Which investment score put this item where it is, and the evidence for THAT
// score (app/ranking.py, D81). The two rest on different evidence, often from
// different tags. An event score rests on its event type and strongest tag. A
// holding score rests on two halves, and shows both: what the article said, and
// why that reaches the company — the quote alone would claim the lab said
// something about the company.
function RankBasis({ item }) {
  const event = item.eventBasis;
  const link = item.holdingBasis;
  const byHolding = item.rankLead === "holding" && link;
  const tag = event?.tag;

  const otherScore = byHolding
    ? `Event score ${item.score.toFixed(1)} · ${event.eventLabel} (weight ${event.eventWeight} of ${event.maxEventWeight})`
      + (tag ? ` × ${tag.label} (${sizing(tag.magnitude, tag.confidence)})` : "")
    : link
    ? `Holding score ${item.holdingScore.toFixed(1)} · ${leadHoldings(link)} via ${link.label}`
    : "No holding link backed by an article tag";

  return (
    <div className="section-block" style={{ paddingTop: 18, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <div className="serif" style={{ fontSize: 34, fontWeight: 500 }}>{item.displayScore}</div>
        <span className="tag-pill" style={bandStyle(item.displayBand)}>{item.displayBand}</span>
        <span style={{ fontSize: 13, color: "var(--muted)" }}>
          {byHolding ? `Set by holding link · ${leadHoldings(link)}` : "Set by event score"}
        </span>
      </div>

      {byHolding ? (
        <>
          <BasisBlock
            heading="What the article says" sign={link.article.sign} label={link.label}
            detail={sizing(link.article.magnitude, link.article.confidence)}
            reason={link.article.reason} quote={link.article.quote}
          />
          {link.company ? (
            <BasisBlock
              heading={`Why it reaches ${link.holding}`} sign={link.company.sign} label={link.holding}
              detail={sizing(link.company.magnitude, link.company.confidence)} reason={link.company.why}
            >
              <SourceLine source={link.company.source} />
            </BasisBlock>
          ) : (
            <BasisBlock
              heading={`Why it reaches ${link.holding}`} label={`Member of ${link.label}`}
              detail="category membership, no company-specific evidence"
            />
          )}
          {/* Holdings tied at the same strength each get their own reason:
              the article half is shared, the company half is not. */}
          {(link.tiedWith || []).map((t) => (
            <BasisBlock
              key={t.isin}
              heading={`Tied at ${t.strength.toFixed(2)} · why it reaches ${t.holding}`}
              sign={t.company?.sign} label={t.holding}
              detail={[t.label !== link.label ? `via ${t.label}` : null,
                t.company ? sizing(t.company.magnitude, t.company.confidence) : `member of ${t.label}`]
                .filter(Boolean).join(" · ")}
              reason={t.company?.why}
            >
              {t.company && <SourceLine source={t.company.source} />}
            </BasisBlock>
          ))}
          <div style={{ fontSize: 12, color: "var(--muted-2)" }}>
            Strength {link.strength.toFixed(2)}
            {link.article.weight != null
              ? ` = ${[link.article.weight, link.company?.weight]
                  .filter((w) => w != null).map((w) => w.toFixed(2)).join(" × ")} × route ${link.routeCeiling}`
              : ""}
          </div>
        </>
      ) : event ? (
        <>
          <div style={{ fontSize: 13 }}>
            {event.eventLabel}
            <span style={{ color: "var(--muted-2)" }}> · weight {event.eventWeight} of {event.maxEventWeight}</span>
          </div>
          {tag && (
            <BasisBlock
              heading="Strongest tag" sign={tag.sign} label={tag.label}
              detail={sizing(tag.magnitude, tag.confidence)} reason={tag.reason} quote={tag.quote}
            />
          )}
        </>
      ) : null}

      <div style={{ fontSize: 12, color: "var(--muted-2)", lineHeight: 1.5 }}>{otherScore}</div>
    </div>
  );
}

// The full record. Takes a decorated item, the related list for its group, and
// the audience whose axis to lead with.
//
// `onOpen` navigates to another item (from Related); `onClose` dismisses. Both
// pages pass the same setter for `onOpen` and a null-setter for `onClose`.
export function DetailPanel({ item, related, audience, onOpen, onClose }) {
  if (!item) return null;
  return (
    <>
      <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.55)" }} onClick={onClose} />
      <div className="detail-panel scroll-y" style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: 620, background: "var(--bg-2)", borderLeft: "1px solid var(--border)", padding: "28px 32px 60px", display: "flex", flexDirection: "column", gap: 22 }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className="label-bracket">{item.labLabel}</span>
            <span style={{ fontSize: 12, color: "var(--muted-2)" }}>{item.date}</span>
          </div>
          <button className="close-btn" onClick={onClose}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M1 1L11 11M11 1L1 11" stroke="#f5f4f1" strokeWidth="1.3" /></svg>
          </button>
        </div>

        <div className="serif" style={{ fontSize: 23, fontWeight: 500, lineHeight: 1.3 }}>{item.title}</div>

        <a href={item.sourceUrl} target="_blank" rel="noreferrer" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted-2)" }}>
          <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M6 4H4a2 2 0 00-2 2v6a2 2 0 002 2h6a2 2 0 002-2v-2M10 2h4v4M14 2L7 9" stroke="#6f6e69" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
          {item.sourceUrl}
        </a>

        <div style={{ fontSize: 14, lineHeight: 1.6, color: "var(--text)" }}>{item.summary}</div>

        {/* Also here, not only on the card: the panel is reachable from a
            folded row, whose own card the reader never saw. */}
        {(related || []).length > 0 && (
          <div className="section-block" style={{ paddingTop: 18, display: "flex", flexDirection: "column", gap: 8 }}>
            <span className="label-bracket">Related</span>
            <RelatedItems items={related} onOpen={onOpen} />
          </div>
        )}

        {item.notableReason && (
          <div className="section-block" style={{ paddingTop: 18, display: "flex", flexDirection: "column", gap: 8 }}>
            <span className="label-bracket">Why flagged</span>
            <div className="quote-block" style={{ fontStyle: "normal", fontFamily: "'Helvetica Neue',Arial,sans-serif", color: "var(--text)", fontSize: 13.5 }}>
              {item.notableReason}
            </div>
          </div>
        )}

        {audience === "investment" && (
          <>
            <RankBasis item={item} />

            {item.mechanisms.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <span className="label-bracket">Evidence</span>
                {item.mechanisms.map((m, i) => (
                  <div key={`m${i}`} style={{ display: "flex", flexDirection: "column", gap: 6, border: "1px solid var(--border)", padding: "12px 14px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                      <span style={{ color: m.color }}>{m.arrow}</span>
                      <span style={{ fontWeight: 600 }}>{m.label}</span>
                      <span style={{ color: "var(--muted-2)" }}>· {m.magnitude} magnitude · {m.confidence} confidence</span>
                    </div>
                    <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>{m.reason}</div>
                    <div className="quote-block">&ldquo;{m.quote}&rdquo;</div>
                  </div>
                ))}
              </div>
            )}

            {item.connectionGroups.length > 0 && (
              <div className="section-block" style={{ paddingTop: 18, display: "flex", flexDirection: "column", gap: 20 }}>
                <span className="label-bracket">Portfolio impact</span>
                {item.connectionGroups.map((group) => (
                  <div key={group.holding} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <div style={{ fontSize: 14, fontWeight: 600 }}>{group.holding}</div>
                    <div style={{ border: "1px solid var(--border)" }}>
                      {group.rows.map((r, i) => (
                        <div
                          key={i}
                          style={{
                            display: "flex", flexDirection: "column", gap: 3,
                            padding: "10px 14px",
                            borderTop: i > 0 ? "1px solid var(--border)" : "none",
                          }}
                        >
                          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                            <span style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
                              <span style={{ color: r.color }}>{r.arrow}</span>
                              {r.label}
                            </span>
                            <span style={{ color: r.color, fontSize: 13, fontWeight: 600, flexShrink: 0 }}>{r.strength.toFixed(2)}</span>
                          </div>
                          {r.caption && (
                            <div style={{ fontSize: 11, color: "var(--muted-2)" }}>{r.caption}</div>
                          )}
                          {r.showNote && r.note && (
                            <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5, marginTop: 2 }}>{r.note}</div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}

        {audience === "ai" && (
          <>
            <div className="section-block" style={{ paddingTop: 18, display: "flex", alignItems: "center", gap: 16 }}>
              <div className="serif" style={{ fontSize: 34, fontWeight: 500 }}>{item.aiChipScore}</div>
              <span className="tag-pill" style={bandStyle(item.aiBand)}>{item.aiBand}</span>
            </div>

            {item.practices.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                <span className="label-bracket">What to do</span>
                {item.practices.map((p, i) => (
                  <div key={i} style={{ display: "flex", flexDirection: "column", gap: 6, border: "1px solid var(--border)", padding: "12px 14px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                      <span className="tag-pill" style={p.actionStyleObj}>{p.action}</span>
                      <span style={{ fontWeight: 600 }}>{p.label}</span>
                      <span style={{ color: "var(--muted-2)" }}>· {p.impact} impact · {p.confidence} confidence</span>
                    </div>
                    <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>{p.reason}</div>
                    <div className="quote-block">&ldquo;{p.quote}&rdquo;</div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
