"use client";

import { useEffect, useMemo, useState } from "react";

import { Gate, apiFetch, signOut } from "./auth";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
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
  if (band === "low") return { background: "transparent", color: MUTED, borderColor: "#333331" };
  return { background: "transparent", color: "#6f6e69", borderColor: "#333331" };
}
function actionStyle(action) {
  if (action === "adopt") return { background: ACCENT, color: "#0d0d0d", borderColor: ACCENT, fontWeight: 600 };
  if (action === "investigate") return { background: "transparent", color: ACCENT, borderColor: ACCENT };
  return { background: "transparent", color: MUTED, borderColor: "#333331" };
}

function LogoMark({ size = 72, fill = "#f5f4f1", accent = ACCENT }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width={size} viewBox="0 0 72 36" fill="none">
      <path d="M66.3857 1.36043L58.1165 34.525H1.27521L9.54438 1.36043H66.3857ZM67.6062 0.383301H8.81065L8.56725 1.36043L0.298081 34.525L0.0546875 35.5022H58.8503L59.0937 34.525L67.3628 1.36043L67.6062 0.383301Z" fill={fill} />
      <path d="M23.7998 14.7238C23.6923 14.5107 23.5342 14.3268 23.3264 14.1731C23.1158 14.0185 22.8538 13.8906 22.5367 13.7876C22.908 13.5664 23.1913 13.315 23.385 13.0343C23.5786 12.7536 23.6755 12.4631 23.6755 12.1629C23.6755 11.4833 23.4223 10.9832 22.9169 10.6625C22.4105 10.3428 21.6279 10.1829 20.5691 10.1829H17.3348L17.1758 10.8198L17.9193 10.9957L18.2444 11.072V16.9775L17.3472 17.1906L17.1784 17.8666H21.15C22.0197 17.8666 22.7046 17.6517 23.2073 17.2209C23.7092 16.79 23.9597 16.1993 23.9597 15.4478C23.9597 15.1795 23.9064 14.9379 23.7998 14.7247V14.7238ZM19.9277 11.1316C20.0148 11.1156 20.1196 11.104 20.2422 11.096C20.3648 11.088 20.4767 11.0845 20.5797 11.0845C21.4574 11.0845 21.8962 11.4798 21.8962 12.2704C21.8962 12.6577 21.7603 12.9623 21.4867 13.1835C21.214 13.4047 20.832 13.5158 20.3426 13.5158H19.9277V11.1325V11.1316ZM21.7301 16.6515C21.4458 16.8611 20.991 16.9659 20.3665 16.9659H20.069C20.0139 16.9659 19.9668 16.9624 19.9268 16.9544V14.4165H20.3532C20.9626 14.4165 21.4147 14.5195 21.7114 14.7247C22.009 14.9308 22.1565 15.2426 22.1565 15.6619C22.1565 16.1123 22.0143 16.4427 21.7301 16.6523V16.6515Z" fill={fill} />
      <path d="M27.4814 16.9775V14.4618V11.0641H28.32L28.5403 10.1829H24.9587L24.7393 11.0641H25.7972V11.2888V16.9775L24.8494 17.2031V17.8666H26.6322H27.6058H28.4301V17.2031L27.8074 17.0547L27.4814 16.9775Z" fill={fill} />
      <path d="M34.3926 11.085L34.4067 11.1431L34.3926 11.085Z" fill={fill} />
      <path d="M33.0794 11.0845V16.9775L34.0281 17.2031V17.8666H30.4474V17.2031L31.3961 16.9775V11.0845H29.2891L29.5138 10.1829H35.4992V11.748H34.5611L34.3959 11.0845H33.0794Z" fill={fill} />
      <path d="M38.7353 21.4429H37.8105V20.9446L38.7353 20.6958L39.0195 19.4629H39.708V20.8149H41.3797V21.1471L41.2136 21.4438H39.708V25.3337C39.708 25.5788 39.7568 25.7387 39.8554 25.8142C39.9549 25.8897 40.1024 25.927 40.3004 25.927C40.5305 25.927 40.8459 25.8755 41.2492 25.7734L41.3682 26.1527C41.3762 26.1527 41.3131 26.1997 41.179 26.2948C41.044 26.3898 40.8743 26.4867 40.6682 26.5853C40.463 26.6839 40.2454 26.7336 40.0162 26.7336C39.5108 26.7336 39.1705 26.611 38.9973 26.3659C38.8223 26.1216 38.7362 25.8045 38.7362 25.4172V21.4447L38.7353 21.4429Z" fill={fill} />
      <path d="M21.6111 26.0673C21.3099 26.3124 21.0194 26.4945 20.7396 26.6127C20.4589 26.7308 20.1169 26.7903 19.7136 26.7903C19.2313 26.7903 18.7987 26.682 18.415 26.4643C18.0321 26.2476 17.7301 25.9234 17.508 25.4917C17.2868 25.0608 17.1758 24.5332 17.1758 23.9087C17.1758 23.4814 17.2335 23.0773 17.3472 22.6935C17.4618 22.3107 17.6315 21.9704 17.858 21.6737C18.0827 21.3771 18.3634 21.1417 18.6992 20.9675C19.035 20.7934 19.4214 20.7073 19.8549 20.7073C20.2582 20.7073 20.594 20.7766 20.8631 20.9151C21.1314 21.0537 21.333 21.2349 21.4672 21.4606C21.6022 21.6862 21.6688 21.9287 21.6688 22.1898C21.6688 22.316 21.6217 22.4386 21.5267 22.5576C21.4325 22.6757 21.2966 22.7353 21.1234 22.7353C20.9733 22.7353 20.8462 22.7077 20.7441 22.6526L20.554 21.5378C20.2857 21.4428 20.0281 21.3957 19.7838 21.3957C19.3885 21.3957 19.0759 21.4845 18.8467 21.6622C18.6166 21.8399 18.4549 22.1028 18.3599 22.451C18.2657 22.7992 18.2178 23.238 18.2178 23.7675C18.2178 24.2969 18.2897 24.7339 18.4372 25.0546C18.5828 25.3744 18.7987 25.6062 19.0838 25.7484C19.3672 25.8905 19.7163 25.9624 20.1267 25.9624C20.506 25.9624 20.9493 25.8798 21.4547 25.7137L21.6093 26.069L21.6111 26.0673Z" fill={fill} />
      <path d="M24.2224 26.7672C23.8191 26.7672 23.4949 26.6997 23.2497 26.5656C23.0045 26.4315 22.8251 26.2511 22.7105 26.0264C22.5959 25.8017 22.5391 25.5583 22.5391 25.2971C22.5391 24.9649 22.6181 24.6789 22.7762 24.4372C22.9335 24.1956 23.1689 23.9966 23.4815 23.8385C23.7933 23.6804 24.1869 23.5694 24.6612 23.5063L25.7289 23.3517V22.4626C25.7289 22.1143 25.6286 21.8621 25.4269 21.7039C25.2253 21.5458 24.9384 21.4668 24.567 21.4668C24.3619 21.4668 24.1167 21.4979 23.8315 21.5609L23.6539 22.6518C23.5348 22.7068 23.4087 22.7344 23.2746 22.7344C23.1404 22.7344 23.0152 22.6855 22.9015 22.586C22.7869 22.4874 22.73 22.3551 22.73 22.189C22.73 21.9438 22.8224 21.7279 23.0081 21.5423C23.1946 21.3566 23.4229 21.2029 23.6965 21.0804C23.9701 20.9578 24.2402 20.8654 24.5093 20.8014C24.7776 20.7384 24.9908 20.7073 25.1489 20.7073C25.6632 20.7073 26.0505 20.8414 26.3117 21.1106C26.5719 21.3797 26.7025 21.6995 26.7025 22.0717V25.8781L27.5437 26.0442V26.4945C27.3305 26.5736 27.1325 26.6331 26.9512 26.6722C26.77 26.7122 26.5719 26.7317 26.3587 26.7317C26.1456 26.7317 25.991 26.6722 25.9199 26.5541C25.8489 26.435 25.8053 26.3124 25.7893 26.1863H25.7423C25.7023 26.2334 25.609 26.3027 25.4633 26.3942C25.3168 26.4848 25.1373 26.57 24.9241 26.6491C24.711 26.7282 24.4773 26.7681 24.2242 26.7681L24.2224 26.7672ZM24.6736 26.0202C24.9739 26.0202 25.3257 25.9491 25.7281 25.8061V23.9211C25.2377 23.9522 24.8353 24.0153 24.5191 24.1103C24.2028 24.2054 23.9683 24.3333 23.8138 24.4959C23.6592 24.6584 23.5819 24.8654 23.5819 25.1186C23.5819 25.3717 23.6743 25.5751 23.8608 25.7528C24.0465 25.9305 24.3174 26.0193 24.6736 26.0193V26.0202Z" fill={fill} />
      <path d="M28.0749 28.6408V28.1424L28.893 27.9763V21.8097L27.9443 21.6436V21.1577L29.7112 20.8255L29.8417 21.3123L29.8533 21.3478C30.0834 21.1497 30.349 20.9943 30.6545 20.8797C30.9583 20.7651 31.2453 20.7083 31.5144 20.7083C32.2259 20.7083 32.7687 20.9392 33.1444 21.402C33.5202 21.8648 33.7076 22.5346 33.7076 23.4122C33.7076 24.0758 33.6001 24.6532 33.3869 25.1435C33.1737 25.6339 32.8735 26.0132 32.4853 26.2814C32.098 26.5506 31.6397 26.6847 31.1102 26.6847C30.635 26.6847 30.2202 26.5506 29.8657 26.2814V27.9772L30.897 28.1193V28.6408H28.074H28.0749ZM31.0401 25.9963C31.6015 25.9963 32.0092 25.816 32.2615 25.4562C32.5146 25.0964 32.6408 24.5253 32.6408 23.7427C32.6408 22.9601 32.5217 22.3871 32.2846 22.0469C32.0483 21.7076 31.6485 21.537 31.0871 21.537C30.8739 21.537 30.6687 21.5512 30.4707 21.5788C30.2735 21.6072 30.0718 21.6525 29.8666 21.7156V25.6996C30.0158 25.7867 30.2006 25.8577 30.4174 25.9137C30.6341 25.9688 30.842 25.9963 31.0401 25.9963Z" fill={fill} />
      <path d="M35.2414 20.8152L35.0131 21.7328L35.4643 21.8119V23.815V26.0331L34.5156 26.1992V26.6851H37.3617V26.1992L36.437 26.0331V20.8152" fill={fill} />
      <path d="M43.8572 26.7672C43.4539 26.7672 43.1296 26.6997 42.8845 26.5656C42.6393 26.4315 42.4599 26.2511 42.3453 26.0264C42.2307 25.8008 42.1738 25.5583 42.1738 25.2971C42.1738 24.9649 42.2529 24.6789 42.411 24.4372C42.5682 24.1956 42.8036 23.9966 43.1163 23.8385C43.4281 23.6804 43.8216 23.5694 44.296 23.5063L45.3637 23.3517V22.4626C45.3637 22.1143 45.2633 21.8621 45.0617 21.7039C44.86 21.5458 44.5731 21.4668 44.2018 21.4668C43.9966 21.4668 43.7514 21.4979 43.4663 21.5609L43.2886 22.6518C43.1696 22.7068 43.0435 22.7344 42.9093 22.7344C42.7752 22.7344 42.65 22.6855 42.5363 22.586C42.4217 22.4874 42.3648 22.3551 42.3648 22.189C42.3648 21.9438 42.4572 21.7279 42.6428 21.5423C42.8294 21.3566 43.0577 21.2029 43.3313 21.0804C43.604 20.9578 43.8749 20.8654 44.1441 20.8014C44.4123 20.7384 44.6255 20.7073 44.7836 20.7073C45.298 20.7073 45.6853 20.8414 45.9464 21.1106C46.2067 21.3797 46.3373 21.6995 46.3373 22.0717V25.8781L47.1785 26.0442V26.4945C46.9653 26.5736 46.7672 26.6331 46.586 26.6722C46.4048 26.7122 46.2067 26.7317 45.9935 26.7317C45.7803 26.7317 45.6258 26.6722 45.5547 26.5541C45.4836 26.435 45.4401 26.3124 45.4241 26.1863H45.377C45.3371 26.2334 45.2438 26.3027 45.0981 26.3942C44.9515 26.4848 44.7721 26.57 44.5589 26.6491C44.3457 26.7282 44.1121 26.7681 43.8589 26.7681L43.8572 26.7672ZM44.3084 26.0202C44.6087 26.0202 44.9604 25.9491 45.3628 25.8061V23.9211C44.8725 23.9522 44.4701 24.0153 44.1538 24.1103C43.8376 24.2054 43.6031 24.3333 43.4485 24.4959C43.294 24.6584 43.2167 24.8654 43.2167 25.1186C43.2167 25.3717 43.3091 25.5751 43.4956 25.7528C43.6813 25.9305 43.9522 26.0193 44.3084 26.0193V26.0202Z" fill={fill} />
      <path d="M47.6416 26.1975L48.5912 26.0314V19.3079L47.6416 19.1417V18.655L49.2912 18.3938L49.563 18.5013V26.0314L50.4886 26.1975V26.6843H47.6416V26.1975Z" fill={fill} />
      <path d="M36.4326 19.9231H35.46L38.6205 7.24438H39.5941L36.4326 19.9231Z" fill={accent} />
    </svg>
  );
}

function FoldedGroup({ members, reason, onOpen }) {
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

function Dashboard() {
  const [audience, setAudience] = useState("investment");
  const [bandFilter, setBandFilter] = useState("all");
  // Two axes a reader actually arrives with: "what has <lab> been doing" and
  // "what has hit <holding>". Both are client-side — /api/items already ships
  // the whole corpus with its connections nested, so filtering here costs a
  // render and filtering server-side would cost a round trip per keystroke.
  const [labFilter, setLabFilter] = useState("all");
  const [docFilter, setDocFilter] = useState("all");
  const [holdingFilter, setHoldingFilter] = useState("all");
  const [sortBy, setSortBy] = useState("score");
  const [selectedId, setSelectedId] = useState(null);

  const [items, setItems] = useState([]);
  const [runStatus, setRunStatus] = useState(null);
  // Count of raised system alerts, for the header badge. Content alerts are the
  // product working; system alerts mean it is broken, and only those belong on
  // a badge that is meant to be noticed.
  const [systemAlerts, setSystemAlerts] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([
      apiFetch("/api/items"),
      apiFetch("/api/status"),
    ])
      .then(([itemsData, statusData]) => {
        setItems(itemsData);
        setRunStatus(statusData);
        setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });

    // Separate and failure-tolerant: a badge that cannot load must not stop the
    // dashboard rendering the corpus.
    // Reads the windowed count from /api/health rather than pulling up to 200
    // full alert bodies to render one integer — and an all-time count could
    // never fall back to zero once anything had ever broken.
    apiFetch("/api/health")
      // Falls back to the pre-rename key: the API and this bundle deploy as
      // separate Render services, so for a few minutes on a sync one of them is
      // behind. Reading only the new key would paint the badge green against an
      // older API — a false green on the health indicator.
      .then((h) => setSystemAlerts(
        h?.unacknowledged_system_alerts ?? h?.recent_system_alerts ?? 0))
      .catch(() => setSystemAlerts(0));
  }, []);

  const decorated = useMemo(() => {
    return items.map((it) => {
      const mechanisms = it.mechanisms.map((t) => ({ ...t, color: signColor(t.sign), arrow: signArrow(t.sign) }));
      const categories = it.categories.map((t) => ({ ...t, color: signColor(t.sign), arrow: signArrow(t.sign) }));
      const connections = it.connections.map((c) => ({
        ...c,
        color: signColor(c.direction),
        arrow: signArrow(c.direction),
        // `note` prefers the holding-specific "why" (app/connect.py) and
        // only falls back to the article's own reason when there isn't one.
        // Category rows never carry a holding-side why (membership has no
        // company-specific evidence), so their note is always identical to
        // what Evidence already shows for that same tag — hide only there.
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
        ? [...mechanisms, ...categories].slice(0, 2).map((t) => ({ label: t.label, color: t.color, arrow: t.arrow }))
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

      const displayScore = (audience === "investment" ? it.score : it.aiScore).toFixed(1);
      const displayBand = audience === "investment" ? it.band : it.aiBand;

      return {
        ...it,
        mechanisms,
        categories,
        connections,
        connectionGroups,
        practices,
        evidencePills,
        impactPills,
        showImpactRow: audience === "investment" && impactPills.length > 0,
        displayScore,
        displayBand,
        displayStyle: bandStyle(displayBand),
        chipScore: it.score.toFixed(1),
        aiChipScore: it.aiScore.toFixed(1),
      };
    });
  }, [items, audience]);

  // Options come from the corpus, not from config: an option that matches
  // nothing is a dead end, and the count next to each one says what is behind
  // it before the reader spends a click finding out.
  //
  // Counted over anchors only, matching what a click actually reveals. Counting
  // folded members too made "OpenAI 23" open 19 cards.
  const labOptions = useMemo(() => {
    const counts = new Map();
    for (const it of decorated) {
      if (anchorFor[it.groupId] !== it) continue;
      if (audience === "investment" ? it.score <= 0 : it.aiScore <= 0) continue;
      const seen = counts.get(it.lab) || { lab: it.lab, label: it.labLabel, n: 0 };
      seen.n += 1;
      counts.set(it.lab, seen);
    }
    return [...counts.values()].sort((a, b) => b.n - a.n);
  }, [decorated, anchorFor, audience]);

  const holdingOptions = useMemo(() => {
    const counts = new Map();
    for (const it of decorated) {
      if (anchorFor[it.groupId] !== it) continue;
      if (it.score <= 0) continue;
      // One count per article, not per connection: an article linked to a
      // holding by three routes is still one thing that happened to it.
      for (const holding of new Set(it.connections.map((c) => c.holding))) {
        counts.set(holding, (counts.get(holding) || 0) + 1);
      }
    }
    return [...counts.entries()]
      .map(([holding, n]) => ({ holding, n }))
      .sort((a, b) => b.n - a.n || a.holding.localeCompare(b.holding));
  }, [decorated, anchorFor]);

  // A holding filter has no meaning on the AI side — those items carry
  // practices, not connections — so it is dropped rather than left set and
  // silently emptying the list.
  const holdingActive = audience === "investment" && holdingFilter !== "all";

  // Lab options are recomputed per audience, so a lab with items on one side and
  // none on the other survives the switch as a value with no matching <option>.
  // The select then renders as "All labs", the filter chip resolves to undefined
  // and is dropped, and the reader sees "0 items" with nothing named as the
  // cause. Reset both filters on the switch rather than trying to render a
  // selection that no longer exists.
  function switchAudience(next) {
    if (next === audience) return;
    setAudience(next);
    setLabFilter("all");
    setHoldingFilter("all");
    setSelectedId(null);
  }

  // Which member of a near-duplicate group speaks for it, decided per audience
  // rather than read from `isAnchor`. That flag is picked once over the whole
  // corpus on a single score; event_type is a multiplicative term in the
  // investment score and absent from the AI score, so the member ranking
  // highest overall can score zero on the axis being displayed — and the member
  // carrying the signal for this audience is the one that got folded.
  //
  // Folded members stay in `decorated` so a reader can expand a card and check
  // the merge. Dropping them would make a collapse look like an article we
  // never had.
  const [anchorFor, foldedByGroup] = useMemo(() => {
    const value = (it) => (audience === "investment" ? it.score : it.aiScore) || 0;
    const best = {};
    for (const it of decorated) {
      const cur = best[it.groupId];
      if (!cur || value(it) > value(cur) || (value(it) === value(cur) && it.date < cur.date)) {
        best[it.groupId] = it;
      }
    }
    const folded = {};
    for (const it of decorated) {
      if (best[it.groupId] === it) continue;
      (folded[it.groupId] = folded[it.groupId] || []).push(it);
    }
    for (const list of Object.values(folded)) list.sort((a, b) => b.date.localeCompare(a.date));
    return [best, folded];
  }, [decorated, audience]);

  const visible = useMemo(() => {
    const anchors = decorated.filter((it) => anchorFor[it.groupId] === it);
    const relevant = anchors.filter((it) => (audience === "investment" ? it.score > 0 : it.aiScore > 0));
    const byBand = bandFilter === "all"
      ? relevant
      : relevant.filter((it) => (audience === "investment" ? it.band : it.aiBand) === bandFilter);
    const byLab = labFilter === "all" ? byBand : byBand.filter((it) => it.lab === labFilter);
    // Papers and announcements are one corpus scored by one rule, so they rank
    // in one list by default. The filter is here because "what has the lab
    // published" and "what has the lab written up" are different questions.
    const byDoc = docFilter === "all" ? byLab : byLab.filter((it) => it.docType === docFilter);
    const byHolding = !holdingActive
      ? byDoc
      : byDoc.filter((it) => it.connections.some((c) => c.holding === holdingFilter));

    const sorted = [...byHolding].sort((a, b) => {
      if (sortBy === "date") return b.date.localeCompare(a.date);
      const av = audience === "investment" ? a.score : a.aiScore;
      const bv = audience === "investment" ? b.score : b.aiScore;
      return bv - av;
    });

    // With a holding selected, the card's impact pills must lead with that
    // holding. Otherwise a list filtered to NVIDIA can show two pills naming
    // other companies, and the reader has to open every card to see why it is
    // in the list at all.
    if (!holdingActive) return sorted;
    return sorted.map((it) => {
      const mine = it.connections.filter((c) => c.holding === holdingFilter);
      const rest = it.connections.filter((c) => c.holding !== holdingFilter);
      return {
        ...it,
        impactPills: [...mine, ...rest].slice(0, 2).map((c) => ({
          label: `${c.holding} ${c.strength.toFixed(2)}`, color: c.color,
        })),
        showImpactRow: true,
      };
    });
  }, [decorated, anchorFor, audience, bandFilter, labFilter, docFilter, holdingFilter, holdingActive, sortBy]);

  const selected = decorated.find((it) => it.id === selectedId) || null;

  const bandOptions = ["all", "high", "medium", "low"];
  const sortOptions = ["score", "date"];

  const lastRunLabel = runStatus
    ? `Last run ${runStatus.status} · $${runStatus.cost_usd.toFixed(2)}`
    : "No runs recorded";

  return (
    <div className="app">
      <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
        <div style={{ height: 64, flex: "0 0 auto", display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 24px", borderBottom: "1px solid var(--border)", background: "var(--bg-2)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            <LogoMark size={48} />
            <div className="toggle-track">
              <div className="toggle-thumb" style={{ transform: audience === "ai" ? "translateX(100%)" : "translateX(0%)" }} />
              <button className="toggle-seg" onClick={() => switchAudience("investment")}>Investment</button>
              <button className="toggle-seg" onClick={() => switchAudience("ai")}>AI team</button>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--muted)" }}>
              <span style={{ width: 6, height: 6, background: runStatus?.status === "failed" ? NEGATIVE : ACCENT, display: "inline-block" }} />
              {loading ? "Loading…" : lastRunLabel}
            </div>
            <a className="btn btn-ghost" href="/digest/" style={{ padding: "8px 14px", textDecoration: "none" }}>Alerts</a>
            <a className="btn btn-ghost" href="/register/" style={{ padding: "8px 14px", textDecoration: "none" }}>Register</a>
            <a className="btn btn-ghost" href="/pipeline/" style={{ padding: "8px 14px", textDecoration: "none" }}>Pipeline</a>
            {/* The health surface is a link rather than a tab: the dashboard
                answers "what did we learn", /ops answers "can I trust it". */}
            <a className="btn btn-ghost" href="/ops/" style={{ padding: "8px 14px", textDecoration: "none", display: "flex", alignItems: "center", gap: 8, borderColor: systemAlerts ? NEGATIVE : undefined, color: systemAlerts ? NEGATIVE : undefined }}>
              Health
              {systemAlerts ? <span style={{ fontSize: 11 }}>{systemAlerts}</span> : null}
            </a>
            <button className="btn btn-ghost" style={{ padding: "8px 14px" }} onClick={signOut}>Sign out</button>
          </div>
        </div>

        <div style={{ flex: "1 1 auto", display: "flex", overflow: "hidden" }}>
          <div className="sidebar" style={{ width: 220, flex: "0 0 220px", borderRight: "1px solid var(--border)", background: "var(--bg-2)", padding: "24px 20px", display: "flex", flexDirection: "column", gap: 28 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span className="label-bracket">Band</span>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {bandOptions.map((v) => {
                  const active = bandFilter === v;
                  const label = v === "all" ? "All" : v.charAt(0).toUpperCase() + v.slice(1);
                  return (
                    <button
                      key={v}
                      className="btn"
                      style={{
                        textAlign: "left", padding: "8px 10px", fontWeight: 500,
                        background: active ? ACCENT : "transparent",
                        color: active ? "#0d0d0d" : "var(--muted)",
                        borderColor: active ? ACCENT : "var(--border)",
                      }}
                      onClick={() => setBandFilter(v)}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span className="label-bracket">Source</span>
              <select
                className="field-input"
                value={docFilter}
                onChange={(e) => setDocFilter(e.target.value)}
                style={{ padding: "8px 10px", fontSize: 13 }}
              >
                <option value="all">Everything</option>
                <option value="announcement">Announcements</option>
                <option value="paper">Papers</option>
              </select>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span className="label-bracket">Lab</span>
              <select
                className="field-input"
                value={labFilter}
                onChange={(e) => setLabFilter(e.target.value)}
                style={{ padding: "8px 10px", fontSize: 13 }}
              >
                <option value="all">All labs</option>
                {labOptions.map((o) => (
                  <option key={o.lab} value={o.lab}>{o.label} ({o.n})</option>
                ))}
              </select>
            </div>

            {/* Investment side only: AI-team items carry practices, not
                holdings, so the control is removed rather than left to filter
                against a field that isn't there. */}
            {audience === "investment" ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <span className="label-bracket">Holding</span>
                <select
                  className="field-input"
                  value={holdingFilter}
                  onChange={(e) => setHoldingFilter(e.target.value)}
                  style={{ padding: "8px 10px", fontSize: 13 }}
                >
                  <option value="all">All holdings</option>
                  {holdingOptions.map((o) => (
                    <option key={o.holding} value={o.holding}>{o.holding} ({o.n})</option>
                  ))}
                </select>
              </div>
            ) : null}

            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span className="label-bracket">Sort by</span>
              <div style={{ display: "flex", gap: 14 }}>
                {sortOptions.map((v) => {
                  const active = sortBy === v;
                  return (
                    <button
                      key={v}
                      className="btn"
                      style={{ background: "none", border: "none", padding: 0, color: active ? ACCENT : "var(--muted)", fontWeight: active ? 600 : 400 }}
                      onClick={() => setSortBy(v)}
                    >
                      {v.charAt(0).toUpperCase() + v.slice(1)}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="section-block" style={{ paddingTop: 20, display: "flex", flexDirection: "column", gap: 6 }}>
              <span className="label-bracket">Showing</span>
              <div style={{ fontSize: 13, color: "var(--muted)" }}>{visible.length} items</div>
              {/* An empty list under a filter is an answer, not a failure —
                  but only if it says which filter produced it. */}
              {bandFilter !== "all" || labFilter !== "all" || docFilter !== "all" || holdingActive ? (
                <>
                  <div style={{ fontSize: 11, color: "var(--muted-2)", lineHeight: 1.6 }}>
                    {[
                      bandFilter !== "all" ? `${bandFilter} band` : null,
                      labFilter !== "all" ? labOptions.find((o) => o.lab === labFilter)?.label : null,
                      docFilter !== "all" ? `${docFilter}s` : null,
                      holdingActive ? holdingFilter : null,
                    ].filter(Boolean).join(" · ")}
                  </div>
                  <button
                    className="btn"
                    style={{ background: "none", border: "none", padding: 0, color: ACCENT, textAlign: "left", fontSize: 11 }}
                    onClick={() => { setBandFilter("all"); setLabFilter("all"); setDocFilter("all"); setHoldingFilter("all"); }}
                  >
                    Clear filters
                  </button>
                </>
              ) : null}
            </div>
          </div>

          <div className="scroll-y" style={{ flex: "1 1 auto", padding: "24px 32px", display: "flex", flexDirection: "column", gap: 12 }}>
            {error && (
              <div style={{ color: NEGATIVE, fontSize: 13 }}>
                Couldn&apos;t reach the API at {API} — is `uv run uvicorn api.main:app --port 8000` running?
              </div>
            )}
            {!error && !loading && visible.length === 0 && (
              <div style={{ color: "var(--muted)", fontSize: 13, padding: "40px 0" }}>No items match this filter.</div>
            )}
            {visible.map((item) => (
              <div key={item.id} className="card" style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: 10 }} onClick={() => setSelectedId(item.id)}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span className="label-bracket">{item.labLabel}</span>
                    <span style={{ fontSize: 12, color: "var(--muted-2)" }}>{item.date}</span>
                  </div>
                  <span className="tag-pill" style={item.displayStyle}>{item.displayScore} · {item.displayBand}</span>
                </div>
                <div className="serif" style={{ fontSize: 17, fontWeight: 500, lineHeight: 1.35 }}>{item.title}</div>
                <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>{item.summary}</div>
                {item.evidencePills.length > 0 && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {item.evidencePills.map((tag, i) => (
                      <span key={i} className="tag-pill" style={{ color: tag.color, borderColor: tag.color }}>{tag.arrow} {tag.label}</span>
                    ))}
                  </div>
                )}
                {item.showImpactRow && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {item.impactPills.map((p, i) => (
                      <span key={i} className="conn-pill" style={{ color: p.color, borderColor: p.color }}>→ {p.label}</span>
                    ))}
                  </div>
                )}
                {(foldedByGroup[item.groupId] || []).length > 0 && (
                  <FoldedGroup
                    members={foldedByGroup[item.groupId]}
                    reason={item.groupReason}
                    onOpen={setSelectedId}
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      </div>

      {selected && (
        <>
          <div style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.55)" }} onClick={() => setSelectedId(null)} />
          <div className="detail-panel scroll-y" style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: 620, background: "var(--bg-2)", borderLeft: "1px solid var(--border)", padding: "28px 32px 60px", display: "flex", flexDirection: "column", gap: 22 }}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className="label-bracket">{selected.labLabel}</span>
                <span style={{ fontSize: 12, color: "var(--muted-2)" }}>{selected.date}</span>
              </div>
              <button className="close-btn" onClick={() => setSelectedId(null)}>
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M1 1L11 11M11 1L1 11" stroke="#f5f4f1" strokeWidth="1.3" /></svg>
              </button>
            </div>

            <div className="serif" style={{ fontSize: 23, fontWeight: 500, lineHeight: 1.3 }}>{selected.title}</div>

            <a href={selected.sourceUrl} target="_blank" rel="noreferrer" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted-2)" }}>
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none"><path d="M6 4H4a2 2 0 00-2 2v6a2 2 0 002 2h6a2 2 0 002-2v-2M10 2h4v4M14 2L7 9" stroke="#6f6e69" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
              {selected.sourceUrl}
            </a>

            <div style={{ fontSize: 14, lineHeight: 1.6, color: "var(--text)" }}>{selected.summary}</div>

            {selected.notableReason && (
              <div className="section-block" style={{ paddingTop: 18, display: "flex", flexDirection: "column", gap: 8 }}>
                <span className="label-bracket">Why flagged</span>
                <div className="quote-block" style={{ fontStyle: "normal", fontFamily: "'Helvetica Neue',Arial,sans-serif", color: "var(--text)", fontSize: 13.5 }}>
                  {selected.notableReason}
                </div>
              </div>
            )}

            {audience === "investment" && (
              <>
                <div className="section-block" style={{ paddingTop: 18, display: "flex", alignItems: "center", gap: 16 }}>
                  <div className="serif" style={{ fontSize: 34, fontWeight: 500 }}>{selected.chipScore}</div>
                  <span className="tag-pill" style={bandStyle(selected.band)}>{selected.band}</span>
                </div>

                {(selected.mechanisms.length > 0 || selected.categories.length > 0) && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                    <span className="label-bracket">Evidence</span>
                    {selected.mechanisms.map((m, i) => (
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
                    {selected.categories.map((c, i) => (
                      <div key={`c${i}`} style={{ display: "flex", flexDirection: "column", gap: 6, border: "1px solid var(--border)", padding: "12px 14px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                          <span style={{ color: c.color }}>{c.arrow}</span>
                          <span style={{ fontWeight: 600 }}>{c.label}</span>
                          <span style={{ color: "var(--muted-2)" }}>· {c.confidence} confidence</span>
                        </div>
                        <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.5 }}>{c.reason}</div>
                        <div className="quote-block">&ldquo;{c.quote}&rdquo;</div>
                      </div>
                    ))}
                  </div>
                )}

                {selected.connectionGroups.length > 0 && (
                  <div className="section-block" style={{ paddingTop: 18, display: "flex", flexDirection: "column", gap: 20 }}>
                    <span className="label-bracket">Portfolio impact</span>
                    {selected.connectionGroups.map((group) => (
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
                  <div className="serif" style={{ fontSize: 34, fontWeight: 500 }}>{selected.aiChipScore}</div>
                  <span className="tag-pill" style={bandStyle(selected.aiBand)}>{selected.aiBand}</span>
                </div>

                {selected.practices.length > 0 && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                    <span className="label-bracket">What to do</span>
                    {selected.practices.map((p, i) => (
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
      )}
    </div>
  );
}


export default function Page() {
  return (
    <Gate>
      <Dashboard />
    </Gate>
  );
}
