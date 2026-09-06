/**
 * Smoke-test that the digest ("Alerts") component can actually be evaluated.
 *
 * The sibling of tests/smoke_dashboard_render.js, and it exists for the same
 * reason at a new site. That file was written after a use-before-declaration
 * fault shipped: a `useMemo` dependency array referenced a hook declared fifty
 * lines below it, so `Dashboard()` threw on first render and every
 * authenticated user saw a blank page — while `next build` passed throughout,
 * because building never calls the component.
 *
 * `DigestView()` has since grown the same shape. It now chains five hooks whose
 * order is load-bearing:
 *
 *     decorated  ->  anchorFor  ->  relatedForGroup  ->  [byUrl, byId]  ->  selected
 *
 * Each reads the one before it in its dependency array. Reordering any two of
 * them is a blank Alerts page and a green build, which is precisely the trade
 * this file refuses.
 *
 * Rather than mount React, this evaluates the component body with the hooks
 * stubbed to run eagerly, which is exactly what surfaces a declaration-order
 * fault. Anything needing a real React runtime is out of scope: the question is
 * only "does this function run".
 *
 * Usage: node tests/smoke_digest_render.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.join(__dirname, '..', 'frontend', 'app', 'digest', 'page.js');
const src = fs.readFileSync(file, 'utf8');

const start = src.indexOf('function DigestView() {');
if (start === -1) {
  console.error('FAIL: DigestView() not found in frontend/app/digest/page.js');
  process.exit(1);
}
const bodyStart = src.indexOf('\n', start) + 1;
const returnAt = src.indexOf('\n  return (', bodyStart);
if (returnAt === -1) {
  console.error('FAIL: could not find the end of the DigestView hook section');
  process.exit(1);
}
const body = src.slice(bodyStart, returnAt);

// The shared decoration is loaded for real, not stubbed — stubbing it would let
// this pass while the page threw on a field detail.js had renamed. All three are
// JSX-free, so they evaluate as plain functions.
const shared = fs.readFileSync(
  path.join(__dirname, '..', 'frontend', 'app', 'detail.js'), 'utf8');

function sharedFunction(name) {
  const at = shared.indexOf(`export function ${name}(`);
  if (at === -1) {
    console.error(`FAIL: ${name}() not found in frontend/app/detail.js`);
    console.error('  digest/page.js imports it; Alerts throws on first render.');
    process.exit(1);
  }
  const end = shared.indexOf('\n}\n', at);
  return shared.slice(at + 'export '.length, end + 2);
}

// `useState(initial)` returns the initial value, so `corpus` is [] and `kind` is
// "investment" — enough for every hook to run. The assertions that need real
// data live in the dashboard smoke test, which exercises the same functions.
const sandbox = {
  useMemo: (fn) => fn(),
  useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}],
  useEffect: () => {},
  apiFetch: async () => ({}),
  signOut: () => {},
  ACCENT: '#5ac3f0',
  NEGATIVE: '#f2545b',
  MUTED: '#9a9992',
  console,
};
sandbox.globalThis = sandbox;

try {
  vm.createContext(sandbox);
  for (const name of ['decorateItems', 'groupAnchors', 'relatedByGroup']) {
    vm.runInContext(sharedFunction(name), sandbox, { timeout: 5000 });
  }
  vm.runInContext(`(function () {\n${body}\n})()`, sandbox, { timeout: 5000 });
} catch (error) {
  // Matched on the message, not `instanceof ReferenceError`: the error is
  // constructed inside the vm context, which has its own realm and therefore
  // its own `ReferenceError`, so `instanceof` is false across the boundary.
  if (/before initialization|is not defined/.test(error.message)) {
    console.error(`FAIL: ${error.message}`);
    console.error('  A hook is referenced in a dependency array above its own declaration.');
    console.error('  Alerts throws on first render; `next build` does not catch this.');
    process.exit(1);
  }
  console.error(`INCONCLUSIVE: ${error.constructor.name}: ${error.message}`);
  console.error('  The stubs in this file need updating to match digest/page.js.');
  process.exit(1);
}

console.log('ok - DigestView() hook section evaluates without a declaration-order fault');
