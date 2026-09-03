/**
 * Smoke-test the built report by running its inlined script against a stub DOM.
 *
 * The report is a single self-contained file with no build step and no browser
 * in CI, so a runtime error inside it is invisible until someone opens it. This
 * has already caught a temporal-dead-zone crash and a parseFloat that silently
 * turned "1,406.8 M EUR" into 1.
 *
 * Usage: node tests/smoke_report.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.join(__dirname, '..', 'research', 'bit_capital_overview.html');
const html = fs.readFileSync(file, 'utf8');
const script = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));

// Permissive stub: enough shape for the report's top-level render to run.
const node = () => {
  const n = {
    innerHTML: '', style: {}, dataset: {}, colSpan: 0, rowIndex: 0, cells: [],
    classList: {add(){}, remove(){}, toggle(){}, contains: () => false},
    addEventListener(){}, removeEventListener(){}, appendChild(){}, remove(){},
    insertCell: () => node(), insertRow: () => node(),
    querySelector: () => node(), querySelectorAll: () => [],
    closest: () => null, tBodies: [{rows: [], appendChild(){}}],
    nextElementSibling: null, parentElement: null, textContent: '',
    get onclick(){return null}, set onclick(v){}, set oninput(v){},
  };
  return n;
};

const errors = [];
// Nodes are cached per id so the HTML the report actually renders into each
// section survives for inspection — a fresh stub per call would throw it away.
const nodes = new Map();
const byId = id => {
  if (!nodes.has(id)) nodes.set(id, node());
  return nodes.get(id);
};

const sandbox = {
  console, Date, Math, JSON, parseFloat, parseInt, Object, Array, String, Number, Set, isNaN,
  document: {
    getElementById: byId,
    querySelectorAll: () => [],
    querySelector: () => node(),
    addEventListener(){},
  },
  window: {},
};
sandbox.globalThis = sandbox;

// `const` at the top level of a script is lexical and never lands on the
// sandbox object, so ask the script to hand its internals over explicitly.
const probe = script +
  '\n;globalThis.__exports = {history, positionChart, detailPanel, METRICS};';

try {
  vm.runInNewContext(probe, sandbox, {filename: 'report inline script'});
} catch (e) {
  console.error('FAIL: report script threw on load\n', e.stack);
  process.exit(1);
}

// Every expandable row must be able to render — including its price line.
const {history, positionChart, detailPanel, METRICS} = sandbox.__exports || {};
if (!history || !positionChart) {
  console.error('FAIL: expected history/positionChart to be defined');
  process.exit(1);
}

const cusips = Object.keys(history.positions);
let vendor = 0, implied = 0;
for (const c of cusips) {
  for (const metric of Object.keys(METRICS)) {
    for (const price of [true, false]) {
      let svg;
      try {
        svg = positionChart(history.positions[c], metric, price);
      } catch (e) {
        errors.push(`${c} ${metric} price=${price}: threw ${e.message}`);
        continue;
      }
      if (/NaN|undefined|Infinity/.test(svg)) {
        errors.push(`${c} ${metric} price=${price}: chart contains NaN/undefined/Infinity`);
      }
    }
  }
  try {
    const panel = detailPanel(c);
    if (/undefined|NaN/.test(panel)) errors.push(`${c}: detail panel contains undefined/NaN`);
  } catch (e) {
    errors.push(`${c}: detailPanel threw ${e.message}`);
  }
  history.positions[c].price_source === 'vendor' ? vendor++ : implied++;
}

// Both tables on the Positions tab must actually offer the expanders, and every
// row that claims one must resolve to a history. The first cut of this feature
// wired only the 13F table, leaving the statutory table — the one at the top of
// the page — inert, which no amount of chart testing would have caught.
const positionsHtml = nodes.get('s-positions').innerHTML || '';
for (const [id, label] of [['t1', 'statutory positions'], ['t2', '13F holdings']]) {
  const start = positionsHtml.indexOf(`id="${id}"`);
  if (start < 0) { errors.push(`table ${id} (${label}) not rendered`); continue; }
  const end = positionsHtml.indexOf('</table>', start);
  const rows = [...positionsHtml.slice(start, end).matchAll(/<tr class="exp" data-cusip="([^"]*)"/g)];
  if (!rows.length) {
    errors.push(`table ${id} (${label}) renders no expandable rows`);
    continue;
  }
  const dangling = rows.map(m => m[1]).filter(c => !history.positions[c]);
  if (dangling.length) {
    errors.push(`table ${id}: ${dangling.length} expandable rows point at missing history ` +
                `(e.g. ${dangling[0]})`);
  }
  console.log(`  ${id} (${label}): ${rows.length} expandable rows`);
}

if (errors.length) {
  console.error(`FAIL: ${errors.length} problems`);
  errors.slice(0, 15).forEach(e => console.error('  ' + e));
  process.exit(1);
}
console.log(`OK: script loads; ${cusips.length} position charts render ` +
            `(${vendor} vendor-priced, ${implied} implied) across ` +
            `${Object.keys(METRICS).length} metrics x price on/off`);
