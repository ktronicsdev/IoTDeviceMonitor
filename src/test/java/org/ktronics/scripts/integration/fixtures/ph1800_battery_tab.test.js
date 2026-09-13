// Render-test renderBattery() from site/ph1800/_app.html against fake plant data.
// Runs the dashboard script in a vm with a minimal DOM stub, then drives the renderer.
const fs = require('fs'), vm = require('vm'), path = require('path');

const html = fs.readFileSync(path.join(process.argv[2], 'site/ph1800/_app.html'), 'utf8');
const script = html.match(/<script>([\s\S]*)<\/script>/)[1];

// --- minimal DOM ------------------------------------------------------------
const els = {};
function el(id) {
  if (!els[id]) els[id] = {
    id, innerHTML: '', textContent: '', value: '', dataset: {}, style: {},
    classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
    appendChild() {}, addEventListener() {}, setAttribute() {}, getContext: () => ({}),
    querySelectorAll: () => [], querySelector: () => null, onclick: null, onchange: null,
    remove() {}, focus() {},
  };
  return els[id];
}
const ctx = {
  console,
  document: {
    getElementById: el,
    querySelectorAll: () => [],
    querySelector: () => null,
    addEventListener() {},
    createElement: () => el('_tmp'),
    title: '',
  },
  window: { addEventListener() {}, matchMedia: () => ({ matches: false }) },
  location: { search: '', hash: '', pathname: '/IoTDeviceMonitor/site/ph1800/Gayan-IMH/', href: 'https://x/' },
  navigator: { userAgent: 'node' },
  fetch: async () => ({ ok: false }),
  setInterval: () => 0, setTimeout: () => 0, clearInterval() {}, clearTimeout() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  crypto: { subtle: { digest: async () => new ArrayBuffer(32) } },
  Chart: function () { this.destroy = () => {}; this.update = () => {}; },
  Intl, Date, Math, JSON, String, Number, Object, Array, parseFloat, parseInt, isNaN,
  TextEncoder, Uint8Array, ArrayBuffer, Promise, RegExp, Error, Set, Map,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(script, ctx, { filename: '_app.html' });

// --- fixtures ---------------------------------------------------------------
const day = new Date().toISOString().slice(0, 10);
const at = (h, m) => `${day} ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:00`;
const series = [
  { timestamp: at(9, 0),  battery_v: 53.2, battery_a: -20, battery_w: -1064, pinverter_w: 2200, load_power_w_est: 1100, pgrid_w: 0 },
  { timestamp: at(10, 0), battery_v: 53.8, battery_a: -15, battery_w: -807,  pinverter_w: 2400, load_power_w_est: 1500, pgrid_w: 0 },
  { timestamp: at(19, 0), battery_v: 51.9, battery_a: 18,  battery_w: 934,   pinverter_w: 0,    load_power_w_est: 900,  pgrid_w: 0 },
  { timestamp: at(20, 0), battery_v: 51.2, battery_a: 22,  battery_w: 1126,  pinverter_w: 0,    load_power_w_est: 1100, pgrid_w: 0 },
];
// soc_curve is the publisher's measured voltage->SOC table for THIS pack (check_ph1800
// .soc_curve()). The breakpoints below are the ones the old hardcoded 16S LFP table used, so
// the SOC assertions further down keep their original expected values (52.4 V -> 58%).
const withBattery = {
  latest: { battery_v: { value: '52.4' }, battery_a: { value: '-12.6' }, battery_w: { value: '-660' } },
  series7d: series,
  plant: { batt_cap_w: 5000,
           soc_curve: [[48.0,0],[50.4,20],[51.5,40],[52.5,60],[53.2,78],[53.7,85],
                       [54.2,93],[54.7,97],[55.2,100]] },
  device: {},
};

// DATA/BATT_CAP_W/curTab are top-level `let`s in the dashboard script, so they live in the
// context's lexical scope, NOT on the context object - assign them by running code inside it.
const run = code => vm.runInContext(code, ctx);
ctx.__fx = {};
let fails = 0;
function check(name, cond, extra) {
  if (cond) { console.log(`  PASS  ${name}`); }
  else { console.log(`  FAIL  ${name}${extra ? '\n        ' + extra : ''}`); fails++; }
}

// 1. plant WITH a battery
ctx.__fx.withBattery = withBattery;
run('DATA = __fx.withBattery; BATT_CAP_W = 5000; renderBattery();');
let out = el('battwrap').innerHTML;
console.log('\n[plant with battery]');
check('tab button is shown', el('tabbtn-battery').style.display === '');
check('renders three cards', (out.match(/class="card"/g) || []).length === 3, `got ${(out.match(/class="card"/g) || []).length}`);
check('shows pack voltage 52.4 V', out.includes('52.4'));
check('shows current -12.6 A', out.includes('-12.6'));
check('shows charging state', out.toLowerCase().includes('charging'));
// SOC now comes from plant.soc_curve, measured per pack - see the [measured SOC curve] block.
check('SOC from 52.4 V interpolates to 58%', />58<small[^>]*>%/.test(out), out.match(/>(\d+)<small[^>]*>%/)?.[0]);
check('SOC is labelled estimated', out.includes('(estimated)'));
check('bank capacity 5 kW', out.includes('5 <small>kW</small>'));
check('charged kWh integrated (>0)', /Charged<\/div><div class="v">([1-9]|0\.[1-9])/.test(out), out.match(/Charged<\/div><div class="v">[^<]*/)?.[0]);
check('discharged kWh integrated (>0)', /Discharged<\/div><div class="v">([1-9]|0\.[1-9])/.test(out), out.match(/Discharged<\/div><div class="v">[^<]*/)?.[0]);
check('voltage range from series', out.includes('51.2–53.8'));
check('cells placeholder shown', out.includes('No per-cell data yet.'));
check('placeholder names the KT BMS Monitor', out.includes('KT BMS Monitor'));
check('no undefined/NaN leaked', !/undefined|NaN/.test(out), out.match(/.{40}(undefined|NaN).{40}/)?.[0]);

// 2. PV-only plant -> tab hidden
console.log('\n[PV-only plant]');
ctx.__fx.pvOnly = { latest: { pv_power_w_est: { value: '2000' } }, series7d: [], plant: {}, device: {} };
run("DATA = __fx.pvOnly; curTab = 'live'; renderBattery();");
check('tab button hidden when no battery', el('tabbtn-battery').style.display === 'none');

// 3. with live per-cell data (what the ESP32 module will deliver)
console.log('\n[with per-cell BMS data]');
const mv = [3364, 3464, 3394, 3550, 3427, 3368, 3455, 3453, 3414, 3361, 3388, 3386, 3418, 3435, 3420, 3377];
ctx.__fx.withCells = Object.assign({}, withBattery, {
  bms: { ok: true, packs: [{ name: 'B1', cells: {
    ts: Date.now() - 3 * 60000, mv, pack_v: 54.67, spread: 189,
    high: { n: 4, mv: 3550 }, low: { n: 10, mv: 3361 },
    tmax: { n: 1, c: 31.8 }, tmin: { n: 4, c: 31.5 } } }] },
});
run('DATA = __fx.withCells; BATT_CAP_W = 5000; renderBattery();');
out = el('battwrap').innerHTML;
check('renders all 16 cell tiles', (out.match(/>Cell \d+</g) || []).length === 16, `got ${(out.match(/>Cell \d+</g) || []).length}`);
check('shows pack voltage from cells', out.includes('54.67'));
check('shows spread', out.includes('189'));
check('shows temperatures', out.includes('31.8') && out.includes('31.5'));
check('placeholder is gone', !out.includes('No per-cell data yet.'));
check('no undefined/NaN leaked', !/undefined|NaN/.test(out), out.match(/.{40}(undefined|NaN).{40}/)?.[0]);

// 4. stale cells fall back to the hint
console.log('\n[stale per-cell data]');
ctx.__fx.withCells.bms.packs[0].cells.ts = Date.now() - 300 * 60000;
run('DATA = __fx.withCells; renderBattery();');
out = el('battwrap').innerHTML;
check('stale cells show the down-link hint', out.includes('the BMS link is down'));
check('stale cells do not render tiles', !/>Cell \d+</.test(out));


// 5 & 6. SOC comes from the pack's OWN measured curve (plant.soc_curve), published by
// check_ph1800.soc_curve(). No chemistry model, no per-account label, no per-cell constants -
// so a 24 V pack and a 48 V pack go through the identical code path.
//
// History: a fixed 16S LFP table was applied to every plant, which clamped a 24 V pack to 0%
// and read a lead-acid bank at 13% when it was ~71% charged. Labelling chemistry per account
// was then tried and dropped: hand-maintained, and unlearnable from the data (measured
// 2026-09, LFP and lead plants overlap on every voltage-shape discriminator).
console.log('\n[measured SOC curve]');

ctx.__fx.curve48 = {
  latest: { battery_v: { value: '52.5' }, battery_a: { value: '-10' }, battery_w: { value: '-525' } },
  series7d: [],
  plant: { batt_cap_w: 4800,
           soc_curve: [[49.5,0],[50.9,10],[51.6,20],[52.1,30],[52.5,40],[52.6,50],
                       [52.7,60],[52.8,70],[53.1,80],[53.3,90],[53.7,100]] },
  device: {},
};
check('SOC interpolates off the published curve (52.5 V -> 40%)',
      run('DATA = __fx.curve48; socFromV(52.5);') === 40,
      'got ' + run('DATA = __fx.curve48; socFromV(52.5);'));
check('below the measured range clamps to 0%', run('DATA = __fx.curve48; socFromV(40);') === 0);
check('above the measured range clamps to 100%', run('DATA = __fx.curve48; socFromV(60);') === 100);
check('interpolates between breakpoints (52.05 V -> ~28%)',
      Math.abs(run('DATA = __fx.curve48; socFromV(52.05);') - 28) <= 3,
      'got ' + run('DATA = __fx.curve48; socFromV(52.05);'));

// A 24 V pack: identical code path, no per-cell scaling, no clamping to 0%.
ctx.__fx.curve24 = {
  latest: { battery_v: { value: '25.0' }, battery_a: { value: '8' }, battery_w: { value: '200' } },
  series7d: [],
  plant: { batt_cap_w: 2400,
           soc_curve: [[23.9,0],[24.2,20],[24.7,30],[25.3,40],[25.8,60],[26.1,80],[27.2,100]] },
  device: {},
};
const soc24 = run('DATA = __fx.curve24; socFromV(25.0);');
check('24 V pack reads a sane mid SOC, not clamped (25.0 V -> 30-45%)',
      soc24 >= 30 && soc24 <= 45, 'got ' + soc24);
run('DATA = __fx.curve24; BATT_CAP_W = 2400; renderBattery();');
out = el('battwrap').innerHTML;
check('Battery tab renders for the 24 V pack', out.includes('25'));
check('tab still labels SOC as an estimate', out.includes('Estimated from pack voltage'));
check('tab states the measured range it is relative to', out.includes('23.9'));
check('no undefined/NaN leaked', !/undefined|NaN/.test(out));

// A pack that has not cycled enough: the publisher sends soc_curve:null and the tab must say
// so rather than showing a figure derived from assumptions that were never measured.
ctx.__fx.noCurve = {
  latest: { battery_v: { value: '26.0' }, battery_a: { value: '4' }, battery_w: { value: '104' } },
  series7d: [], plant: { batt_cap_w: 2400, soc_curve: null }, device: {},
};
check('no curve -> no SOC figure', run('DATA = __fx.noCurve; socFromV(26.0);') === null);
run('DATA = __fx.noCurve; BATT_CAP_W = 2400; renderBattery();');
out = el('battwrap').innerHTML;
check('tab explains why SOC is absent', out.includes('not cycled enough'));
check('no undefined/NaN leaked (no curve)', !/undefined|NaN/.test(out));

// An older payload published before soc_curve existed must degrade, not crash.
ctx.__fx.legacy = { latest: {}, series7d: [], plant: { batt_cap_w: 2400 }, device: {} };
check('missing soc_curve degrades to null', run('DATA = __fx.legacy; socFromV(52.0);') === null);


// 7. The Charge/Discharge reliability banner. Charge/Discharge is integrated from the
// whole-amp-quantized `Batt Current`; the publisher reports whether the coulomb count
// actually closes (plant.battery_health), and a plant whose battery books +79 Ah/day is
// physically impossible, so the bars must be labelled rather than shown as fact.
console.log('\n[battery-balance warning banner]');
run('DATA = { plant: { battery_health: { ok: true, reason: "balances" } } }; renderBattWarn();');
check('healthy plant shows no banner', el('battwarn').style.display === 'none');

run('DATA = { plant: { battery_health: { ok: false, ' +
    'reason: "battery current does not balance: +79.3 Ah/day net across 7 days." } } }; renderBattWarn();');
check('flagged plant shows the banner', el('battwarn').style.display === '');
check('banner carries the publisher reason', el('battwarn').innerHTML.includes('+79.3 Ah/day'));
check('banner scopes the damage to the battery bars',
      el('battwarn').innerHTML.includes('Production, Consumption and Grid figures are unaffected'));

run('DATA = { plant: {} }; renderBattWarn();');      // payload published before this field existed
check('missing battery_health degrades silently', el('battwarn').style.display === 'none');

run('DATA = { plant: { battery_health: { ok: null, reason: "not enough cycling days" } } }; renderBattWarn();');
check('undecided (ok:null) shows no banner', el('battwarn').style.display === 'none');

console.log(fails ? `\n${fails} CHECK(S) FAILED` : '\nALL CHECKS PASSED');
process.exit(fails ? 1 : 0);
