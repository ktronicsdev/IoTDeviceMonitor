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
const withBattery = {
  latest: { battery_v: { value: '52.4' }, battery_a: { value: '-12.6' }, battery_w: { value: '-660' } },
  series7d: series, plant: { batt_cap_w: 5000 }, device: {},
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
run('DATA = __fx.withBattery; BATT_CAP_W = 5000; PACK_S = inferPackS(); renderBattery();');
let out = el('battwrap').innerHTML;
console.log('\n[plant with battery]');
check('tab button is shown', el('tabbtn-battery').style.display === '');
check('renders three cards', (out.match(/class="card"/g) || []).length === 3, `got ${(out.match(/class="card"/g) || []).length}`);
check('shows pack voltage 52.4 V', out.includes('52.4'));
check('shows current -12.6 A', out.includes('-12.6'));
check('shows charging state', out.toLowerCase().includes('charging'));
// SOC_TABLE interpolates 51.5V->40% .. 52.5V->60%, so 52.4 V must read 58%.
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
run("DATA = __fx.pvOnly; curTab = 'live'; PACK_S = inferPackS(); renderBattery();");
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
run('DATA = __fx.withCells; BATT_CAP_W = 5000; PACK_S = inferPackS(); renderBattery();');
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
run('DATA = __fx.withCells; PACK_S = inferPackS(); renderBattery();');
out = el('battwrap').innerHTML;
check('stale cells show the down-link hint', out.includes('the BMS link is down'));
check('stale cells do not render tiles', !/>Cell \d+</.test(out));


// 5. pack-series inference: the fleet is mixed 16S/8S and the curve is calibrated 16S.
console.log('\n[pack-series aware SOC]');
check('16S fixture is detected as 16S', run('DATA = __fx.withBattery; inferPackS();') === 16);
check('16S SOC curve is unchanged (52.4 V -> 58%)',
      run('PACK_S = 16; socFromV(52.4);') === 58, 'got ' + run('PACK_S = 16; socFromV(52.4);'));

// Gayan-IMH: a real 8S/24V pack (live range 23.8-27.7 V). On the 16S curve every
// reading clamps to 0%, which is what this scaling exists to fix.
const at8 = (h) => `${day} ${String(h).padStart(2, '0')}:00:00`;
ctx.__fx.pack8s = {
  latest: { battery_v: { value: '27.3' }, battery_a: { value: '-51' }, battery_w: { value: '-1392' } },
  series7d: [
    { timestamp: at8(6),  battery_v: 23.8, battery_w: 300 },
    { timestamp: at8(12), battery_v: 27.7, battery_w: -1200 },
    { timestamp: at8(18), battery_v: 26.1, battery_w: 400 },
  ],
  plant: { batt_cap_w: 2400 }, device: {},
};
check('8S pack is detected as 8S', run('DATA = __fx.pack8s; inferPackS();') === 8,
      'got ' + run('DATA = __fx.pack8s; inferPackS();'));
check('16S curve would have reported 0% for it', run('PACK_S = 16; socFromV(27.3);') === 0);
const soc8 = run('DATA = __fx.pack8s; PACK_S = inferPackS(); socFromV(27.3);');
check('8S curve reports a plausible high SOC (85-100%)', soc8 >= 85 && soc8 <= 100, 'got ' + soc8);
run('DATA = __fx.pack8s; BATT_CAP_W = 2400; PACK_S = inferPackS(); renderBattery();');
out = el('battwrap').innerHTML;
check('Battery tab renders for the 8S plant', out.includes('27.3'));
check('tab states which pack curve is assumed', out.includes('8S LFP curve'));
check('no undefined/NaN leaked', !/undefined|NaN/.test(out));

// A plant with no battery series at all (Thusharasameera) must not crash the inference.
ctx.__fx.noBatt = { latest: {}, series7d: [], plant: {}, device: {} };
check('empty series falls back to 16S', run('DATA = __fx.noBatt; inferPackS();') === 16);

console.log(fails ? `\n${fails} CHECK(S) FAILED` : '\nALL CHECKS PASSED');
process.exit(fails ? 1 : 0);
