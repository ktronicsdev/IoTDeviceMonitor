// Clock tests for both dashboards (site/ph1000/index.html, site/ph1800/_app.html).
//
// The header shows two times: "Actual time (SL)", computed in the BROWSER, and
// "Last update", a plant-LOCAL stamp with no zone suffix that the publisher copies
// out of the device. Both have been wrong before:
//
//   * 2026-09-07: the clock read 11:27:53 while the device stamp read 16:53 - the
//     browser had formatted `timeZone:'Asia/Colombo'` as UTC (managed profile /
//     anti-fingerprint extension), so the clock ran exactly 5h30m slow;
//   * ph1000 parsed the stamp with `new Date(s)`, i.e. in the VIEWER's zone, so from
//     UTC+2 a fresh stamp landed in the future, age went negative and hours-old data
//     kept a green "Online" dot.
//
// So: run the real page script under a fixed clock and assert what the header says,
// once with a working Intl and once with a browser that ignores IANA zones. The
// viewer's own zone comes from TZ in the environment - the caller runs this file
// several times with different zones and every run must agree.
const fs = require('fs'), vm = require('vm'), path = require('path');

const REPO = process.argv[2];
const NOW = Date.UTC(2026, 8, 7, 11, 27, 53);          // the instant in the bug report
const SL_AT_NOW = '2026-09-07 16:57:53';               // ...which is this in Colombo
const FRESH = '2026-09-07 16:53:01';                   // device stamp from that screenshot
const STALE = '2026-09-07 06:53:01';                   // same day, 10h old

let fails = 0;
const check = (name, cond, extra) => {
  if (cond) console.log('  PASS  ' + name);
  else { console.log('  FAIL  ' + name + (extra ? '\n        ' + extra : '')); fails++; }
};

// A browser that ignores the IANA zone and formats everything as UTC.
const realIntl = Intl;
const blindIntl = { DateTimeFormat: function (l, o) {
  return new realIntl.DateTimeFormat(l, Object.assign({}, o, { timeZone: 'UTC' })); } };

function stubDom() {
  const els = {};
  const el = id => (els[id] = els[id] || {
    id, innerHTML: '', textContent: '', value: '', dataset: {}, style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    appendChild() {}, addEventListener() {}, setAttribute() {}, getContext: () => ({}),
    querySelectorAll: () => [], querySelector: () => null, onclick: null, onchange: null,
    remove() {}, focus() {},
  });
  return el;
}

// Load a page with its clock frozen at `now`: `new Date()` and Date.now() are fixed,
// every other Date behaviour (parsing, arithmetic) stays real.
function loadPage(rel, intl, now) {
  const html = fs.readFileSync(path.join(REPO, rel), 'utf8');
  const script = html.match(/<script>([\s\S]*)<\/script>/)[1];
  const FixedDate = class extends Date {
    constructor(...a) { if (a.length === 0) super(now); else super(...a); }
    static now() { return now; }
  };
  const el = stubDom();
  const ctx = {
    console,
    document: { getElementById: el, querySelectorAll: () => [], querySelector: () => null,
      addEventListener() {}, createElement: () => el('_tmp'), title: '' },
    window: { addEventListener() {}, matchMedia: () => ({ matches: false }) },
    location: { search: '', hash: '', pathname: '/IoTDeviceMonitor/site/ph1800/Gayan-IMH/',
      href: 'https://x/' },
    navigator: { userAgent: 'node' },
    fetch: async () => ({ ok: false }),
    setInterval: () => 0, setTimeout: () => 0, clearInterval() {}, clearTimeout() {},
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    crypto: { subtle: { digest: async () => new ArrayBuffer(32) } },
    Chart: function () { this.destroy = () => {}; this.update = () => {}; },
    Intl: intl, Date: FixedDate, Math, JSON, String, Number, Object, Array, parseFloat,
    parseInt, isNaN, TextEncoder, Uint8Array, ArrayBuffer, Promise, RegExp, Error, Set, Map,
  };
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(script, ctx, { filename: path.basename(rel) });
  return { run: code => vm.runInContext(code, ctx), el };
}

function checkPage(rel, intlName, intl) {
  const label = (rel.includes('ph1000') ? 'ph1000' : 'ph1800') + ' | ' + intlName;
  console.log('\n[' + label + '] viewer TZ=' + (process.env.TZ || '(system)'));
  const { run, el } = loadPage(rel, intl, NOW);

  // --- the header clock -----------------------------------------------------
  run('tickSL();');
  const shown = el('sltime').textContent;
  check('clock shows Sri Lanka time, not UTC',
        shown === 'Actual time (SL): ' + SL_AT_NOW, 'got "' + shown + '"');
  check('clock is not the raw UTC wall clock', !shown.includes('11:27:53'), shown);

  // --- plant-local stamps parse to the right instant ------------------------
  const fresh = run('ts(' + JSON.stringify(FRESH) + ').getTime()');
  check('"16:53:01 SL" parses to 11:23:01 UTC',
        fresh === Date.UTC(2026, 8, 7, 11, 23, 1), 'got ' + new Date(fresh).toISOString());

  // --- staleness: a fresh stamp must read a small POSITIVE age --------------
  const age = (NOW - fresh) / 60000;
  check('fresh stamp is ~4.9 min old, never negative (got ' + age.toFixed(1) + ')',
        age > 0 && age < 15);
  check('10h-old stamp is flagged stale',
        (NOW - run('ts(' + JSON.stringify(STALE) + ').getTime()')) / 60000 > 15);

  // --- slClock(): BMS frame timestamps in the Battery pane ------------------
  check('slClock() renders SL wall time',
        run('slClock(' + NOW + ')') === '16:57:53', 'got ' + run('slClock(' + NOW + ')'));

  // --- fixed +05:30, no DST: January and July must both hold ----------------
  check('January stamp keeps the +05:30 offset',
        run('ts("2026-01-15 12:00:00").getTime()') === Date.UTC(2026, 0, 15, 6, 30, 0));
  check('July stamp keeps the +05:30 offset (no DST in Sri Lanka)',
        run('ts("2026-07-15 12:00:00").getTime()') === Date.UTC(2026, 6, 15, 6, 30, 0));

  // --- round trip: format an instant, parse it back -------------------------
  check('clock output parses back to the same instant',
        run('ts(' + JSON.stringify(SL_AT_NOW) + ').getTime()') === NOW);

  // --- a bad stamp must yield NaN, not a bogus time -------------------------
  check('unparseable stamp -> NaN (the header falls back to a dash)',
        run('isNaN(ts("rubbish").getTime())'));
}

for (const page of ['site/ph1000/index.html', 'site/ph1800/_app.html']) {
  checkPage(page, 'normal browser', realIntl);
  checkPage(page, 'browser that ignores IANA zones', blindIntl);
}

// The date must roll over at SL midnight - not at the viewer's, not at UTC's.
console.log('\n[midnight rollover] viewer TZ=' + (process.env.TZ || '(system)'));
for (const page of ['site/ph1000/index.html', 'site/ph1800/_app.html']) {
  const evening = Date.UTC(2026, 8, 7, 18, 35, 0);      // 00:05 on the 8th in Colombo
  const { run, el } = loadPage(page, realIntl, evening);
  run('tickSL();');
  check((page.includes('ph1000') ? 'ph1000' : 'ph1800') +
        ': 18:35 UTC shows as 00:05 on the NEXT day',
        el('sltime').textContent === 'Actual time (SL): 2026-09-08 00:05:00',
        'got "' + el('sltime').textContent + '"');
}

console.log(fails ? '\n' + fails + ' CHECK(S) FAILED' : '\nALL CHECKS PASSED');
process.exit(fails ? 1 : 0);
