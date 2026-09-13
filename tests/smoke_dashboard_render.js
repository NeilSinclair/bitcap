/**
 * Smoke-test that the dashboard component can actually be evaluated.
 *
 * The failure this exists for was shipped and pushed: the near-duplicate work
 * added `anchorFor` to two `useMemo` dependency arrays about fifty lines above
 * the `const [anchorFor, foldedByGroup] = useMemo(...)` that declares it. A
 * dependency array is evaluated during render, so `Dashboard()` threw
 * `ReferenceError: Cannot access 'anchorFor' before initialization` on the very
 * first render, before any data was fetched. Every authenticated user would
 * have seen a blank page.
 *
 * **`next build` passed the whole time**, which is the point of this file.
 * Building type-checks and bundles; it never calls the component, so a temporal
 * dead zone error is invisible to it. "The frontend builds" was offered as
 * evidence the page worked, and it was not evidence of that at all.
 *
 * Rather than mount React, this evaluates the component body with the hooks
 * stubbed: `useMemo`/`useState`/`useEffect` are replaced by versions that run
 * their callbacks eagerly, which is exactly what surfaces a declaration-order
 * fault. Anything that needs a real React runtime is out of scope here — the
 * question being asked is only "does this function run".
 *
 * Usage: node tests/smoke_dashboard_render.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.join(__dirname, '..', 'frontend', 'app', 'page.js');
const src = fs.readFileSync(file, 'utf8');

// The decoration, anchoring and link-resolution moved to frontend/app/detail.js
// so the digest could render the same record. They are loaded here FOR REAL
// rather than stubbed, and the difference matters: stubbing them would fix this
// file's failure while quietly removing ~100 lines of logic from the only thing
// that executes it, which is the same class of blank-page failure this file was
// written to catch. All three are JSX-free, so they evaluate as plain functions.
const shared = fs.readFileSync(
  path.join(__dirname, '..', 'frontend', 'app', 'detail.js'), 'utf8');

function sharedFunction(name) {
  const at = shared.indexOf(`export function ${name}(`);
  if (at === -1) {
    console.error(`FAIL: ${name}() not found in frontend/app/detail.js`);
    console.error('  page.js imports it; the dashboard throws on first render.');
    process.exit(1);
  }
  // To the closing brace in column 0 — these are top-level declarations.
  const end = shared.indexOf('\n}\n', at);
  return shared.slice(at + 'export '.length, end + 2);
}

// The component body, without the JSX return — JSX needs a transform and the
// bug class this catches lives in the hook declarations above it.
const start = src.indexOf('function Dashboard() {');
if (start === -1) {
  console.error('FAIL: Dashboard() not found in frontend/app/page.js');
  process.exit(1);
}
const bodyStart = src.indexOf('\n', start) + 1;
const returnAt = src.indexOf('\n  return (', bodyStart);
if (returnAt === -1) {
  console.error('FAIL: could not find the end of the Dashboard hook section');
  process.exit(1);
}
const body = src.slice(bodyStart, returnAt);

// Hooks stubbed to run eagerly. `useMemo(fn, deps)` evaluating `deps` is what
// makes a use-before-declaration throw, so the deps argument must be a real
// evaluated array and not a lazy one — which it is, by JS call semantics.
const sandbox = {
  useMemo: (fn) => fn(),
  useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}],
  useEffect: () => {},
  apiFetch: async () => ({}),
  signOut: () => {},
  API: 'http://localhost:8000',
  ACCENT: '#5ac3f0',
  NEGATIVE: '#f2545b',
  MUTED: '#9a9992',
  bandStyle: () => ({}),
  actionStyle: () => ({}),
  signColor: () => '#fff',
  signArrow: () => '=',
  console,
};
sandbox.globalThis = sandbox;

vm.createContext(sandbox);

// The shared logic runs in its OWN try/catch, reporting FAIL rather than
// INCONCLUSIVE. The block below treats an unrecognised error as "the stubs no
// longer match the component", which is right for the component body and wrong
// here — a broken assertion in this section is a real regression in the moved
// decoration, and calling it a broken test would send the reader to fix the
// wrong file.
try {
  // The real implementations, so the component body below resolves them the way
  // the browser will.
  for (const name of ['leadHoldings', 'decorateItems', 'groupAnchors', 'relatedByGroup']) {
    vm.runInContext(sharedFunction(name), sandbox, { timeout: 5000 });
  }

  // And executed over one synthetic item, because `Dashboard()` alone calls
  // them with an empty array — every field access inside would stay unreached
  // and a rename in the API contract would pass silently.
  const article = {
    id: 1, groupId: 'g1', groupMethod: 'embedding', date: '2026-09-03',
    title: 'Astra', docType: 'announcement', score: 90, aiScore: 40,
    band: 'high', aiBand: 'low', relatedTo: [{ id: 1, evidence: 'gpt-6' }],
    mechanisms: [{ sign: 'positive', label: 'm', magnitude: 'high',
                   confidence: 'high', reason: 'r', quote: 'q' }],
    categories: [], practices: [{ action: 'adopt', label: 'p', impact: 'high',
                                  confidence: 'high', reason: 'r', quote: 'q' }],
    connections: [{ holding: 'NVIDIA', isin: 'US1', direction: 'positive',
                    strength: 0.9, route: 'mechanism', label: 'l', note: 'n',
                    magnitude: 'high', confidence: 'high' }],
    // The investment rank fields /api/items sends (app/ranking.py, D81).
    eventType: 'frontier_model_release', holdingScore: 90, rankLead: 'event',
    rankValue: 90, rankBand: 'high',
    eventBasis: { eventType: 'frontier_model_release', eventLabel: 'Frontier model release',
                  eventWeight: 5, maxEventWeight: 5, tag: null },
    holdingBasis: { holding: 'NVIDIA', isin: 'US1', route: 'mechanism', label: 'l',
                    direction: 'positive', strength: 0.9, routeCeiling: 1,
                    article: { quote: 'q', weight: 1 }, company: { why: 'w', weight: 0.9 } },
  };
  sandbox.article = article;   // the context is already created; add, don't clone
  vm.runInContext(
    `(function () {
       const rows = decorateItems([article], 'investment');
       if (rows.length !== 1) throw new Error('decorateItems dropped the item');
       if (!rows[0].connectionGroups.length) throw new Error('connectionGroups empty');
       if (rows[0].chipScore !== '90.0') throw new Error('chipScore wrong: ' + rows[0].chipScore);
       const [anchors] = groupAnchors(rows, 'investment');
       if (!anchors.g1) throw new Error('groupAnchors named no anchor');
       relatedByGroup(rows, anchors);
       for (const audience of ['investment', 'ai']) {
         const r = decorateItems([article], audience);
         if (!r[0].displayBand) throw new Error('displayBand empty for ' + audience);
       }

       // D81, investment: a score-0 member never anchors or folds, and the
       // anchor is the best member on (higher score, other score).
       const zero = { ...article, id: 2, score: 0, rankValue: 90, holdingScore: 90 };
       const deal = { ...article, id: 3, score: 40, rankValue: 100, holdingScore: 100,
                      rankLead: 'holding' };
       const group = decorateItems([article, zero, deal], 'investment');
       const [anchor, folded] = groupAnchors(group, 'investment');
       if (anchor.g1.id !== 3) throw new Error('anchor is not the best-ranked member: ' + anchor.g1.id);
       if ((folded.g1 || []).some((r) => r.score <= 0)) throw new Error('a score-0 member was folded in');
       const lead = group.find((r) => r.id === 3);
       if (lead.displayLead !== 'NVIDIA' || lead.displayScore !== '100.0') {
         throw new Error('chip does not name the holding that set the rank: ' + lead.displayLead);
       }
       // A tie at 100 breaks on the other score, not on date: the later member
       // (100 / 67) must beat the earlier one (100 / 17).
       const early = { ...article, id: 4, groupId: 'g2', score: 100, rankValue: 100, holdingScore: 17 };
       const later = { ...article, id: 5, groupId: 'g2', score: 100, rankValue: 100, holdingScore: 67,
                       date: '2026-09-04' };
       const [tie] = groupAnchors(decorateItems([early, later], 'investment'), 'investment');
       if (tie.g2.id !== 5) throw new Error('a tie at 100 did not break on the other score');
       // Holdings tied at the top strength are all named, past three counted.
       const tied = { ...deal, id: 6, groupId: 'g3', holdingBasis: { ...article.holdingBasis,
                      tiedWith: [{ holding: 'Micron' }, { holding: 'Amazon' }, { holding: 'Intel' }] } };
       const tiedLead = decorateItems([tied], 'investment')[0].displayLead;
       if (tiedLead !== 'NVIDIA, Micron, Amazon +1') throw new Error('tied holdings not named: ' + tiedLead);
       const [aiAnchor] = groupAnchors(decorateItems([zero], 'ai'), 'ai');
       if (!aiAnchor.g1) throw new Error('the AI view dropped a score-0 item');
     })()`,
    sandbox, { timeout: 5000 });
} catch (error) {
  console.error(`FAIL: ${error.message}`);
  console.error('  frontend/app/detail.js no longer decorates an item the way');
  console.error('  the dashboard and the digest both read it. Fix detail.js.');
  process.exit(1);
}

try {
  vm.runInContext(`(function () {\n${body}\n})()`, sandbox, { timeout: 5000 });
} catch (error) {
  // Matched on the message, not `instanceof ReferenceError`: the error is
  // constructed inside the vm context, which has its own realm and therefore
  // its own `ReferenceError`, so `instanceof` is false across the boundary.
  // That mis-reported the real bug as INCONCLUSIVE the first time this ran.
  if (/before initialization|is not defined/.test(error.message)) {
    console.error(`FAIL: ${error.message}`);
    console.error('  A hook is referenced in a dependency array above its own declaration.');
    console.error('  The page throws on first render; `next build` does not catch this.');
    process.exit(1);
  }
  // Any other error means the stub set no longer matches the component, which
  // is a broken test rather than a broken page. Say which, loudly, instead of
  // failing in a way that reads as a product bug.
  console.error(`INCONCLUSIVE: ${error.constructor.name}: ${error.message}`);
  console.error('  The stubs in this file need updating to match frontend/app/page.js.');
  process.exit(1);
}

console.log('ok - Dashboard() hook section evaluates without a declaration-order fault');
