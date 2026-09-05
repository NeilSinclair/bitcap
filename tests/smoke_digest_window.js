/**
 * Smoke-test the digest window label across timezones.
 *
 * The label is one line of the digest page and it has been wrong twice, both
 * times silently and both times found by review rather than by anything here:
 *
 *   1. `window_start` is an *exclusive* bound — selection is half-open at date
 *      resolution (`start.date() < published_on <= end.date()` in
 *      app/digest.py) — so rendering it raw advertised a day the digest had
 *      excluded, and a 48-hour window read as three days.
 *   2. The fix for (1) formatted in local time while the boundaries are UTC, so
 *      west of Greenwich both ends landed a day early. Invisible from CET.
 *
 * Neither raised an error; the page just stated the wrong dates for a dated
 * claim. Same reasoning as tests/smoke_report.js: no browser in CI, so a
 * display bug is invisible until someone opens the page and happens to check.
 *
 * Usage: node tests/smoke_digest_window.js
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const file = path.join(__dirname, '..', 'frontend', 'app', 'digest', 'page.js');
const src = fs.readFileSync(file, 'utf8');

// Pull the two formatters out of the module rather than restating them, so the
// test breaks when the page changes and not when a copy here drifts.
const grab = (name) => {
  const m = src.match(new RegExp(`function ${name}\\(iso\\) \\{[\\s\\S]*?\\n\\}`));
  if (!m) throw new Error(`${name}() not found in ${file} — did it get renamed?`);
  return m[0];
};
const context = {};
vm.createContext(context);
vm.runInContext(`${grab('day')}\n${grab('firstCoveredDay')}`, context);

// Every zone/window pair must name the same two days: a 48-hour window covers
// the day before the end and the end itself, everywhere.
const ZONES = ['UTC', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles',
               'Pacific/Auckland', 'Pacific/Kiritimati'];
const CASES = [
  // [window_start, window_end, expected label] — quantised, and rolling at the
  // hours most likely to slip a day under a local-time format.
  ['2026-09-03T00:00:00+00:00', '2026-09-05T00:00:00+00:00', 'Sep 4 — Sep 5'],
  ['2026-09-03T09:27:07+00:00', '2026-09-05T09:27:07+00:00', 'Sep 4 — Sep 5'],
  ['2026-09-03T23:30:00+00:00', '2026-09-05T23:30:00+00:00', 'Sep 4 — Sep 5'],
  ['2026-09-03T00:10:00+00:00', '2026-09-05T00:10:00+00:00', 'Sep 4 — Sep 5'],
  ['2026-08-30T00:00:00+00:00', '2026-09-01T00:00:00+00:00', 'Aug 31 — Sep 1'],
];

const errors = [];
for (const zone of ZONES) {
  process.env.TZ = zone;   // node re-reads this per Date operation
  for (const [start, end, expected] of CASES) {
    const got = `${context.firstCoveredDay(start)} — ${context.day(end)}`;
    if (got !== expected) {
      errors.push(`TZ=${zone} ${start.slice(0, 16)}..${end.slice(0, 16)}: ` +
                  `got "${got}", want "${expected}"`);
    }
  }
}

if (errors.length) {
  console.error(`FAIL: ${errors.length} wrong window labels`);
  errors.slice(0, 15).forEach((e) => console.error('  ' + e));
  process.exit(1);
}
console.log(`OK: window label correct across ${ZONES.length} timezones ` +
            `x ${CASES.length} window shapes`);
