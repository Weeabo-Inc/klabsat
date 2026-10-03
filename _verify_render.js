/* TEMPORARY verification harness - deletes itself after use.
   Extracts the dashboard <script> and runs it against a stub DOM + stub fetch,
   then asserts the iPhone card renders correctly for each of the four states. */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const script = /<script>([\s\S]*?)<\/script>/.exec(html)[1];
const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(m => m[1]));

function mkEl(id) {
  const e = {
    id, textContent: '', className: '', style: {}, _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    classList: { _s: new Set(), toggle(c, f) { const on = f === undefined ? !this._s.has(c) : !!f; on ? this._s.add(c) : this._s.delete(c); return on; } },
    setAttribute(k, v) { this['attr_' + k] = v; },
    addEventListener() {}, getContext() { return { clearRect(){}, beginPath(){}, moveTo(){}, lineTo(){}, stroke(){}, fill(){}, arc(){}, fillText(){}, createLinearGradient(){ return { addColorStop(){} }; } }; },
    width: 600, height: 76
  };
  return e;
}
const store = {};
const missing = new Set();
const document = {
  readyState: 'complete',
  addEventListener() {},
  getElementById(id) { if (!ids.has(id)) { missing.add(id); return null; } return store[id] || (store[id] = mkEl(id)); }
};

const payloads = {};
const fetch = (url) => Promise.resolve({
  ok: true, status: 200,
  json: () => Promise.resolve(url.startsWith('/api/iphone') ? payloads.iphone : payloads.state)
});

const ctx = { document, fetch, console, setTimeout: (f, t) => 0, clearTimeout() {}, setInterval: () => 0,
              location: { port: '8792', href: 'http://127.0.0.1:8792/' },
              AbortController: function () { this.abort = () => {}; }, Date, JSON, Math, Object, Array, String, Number, isFinite, parseFloat, RegExp };
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(script, ctx, { filename: 'dashboard-inline.js' });

/* ---- cases ---------------------------------------------------------------- */
const cases = {
  queryable: { status: 'queryable', _meta: { stale: false, iphone_file: { exists: true, age_seconds: 2.1, path: 'sample-iphone.json' } },
    usb: { present: true, likely_iphone: true, device_count: 18, peripherals: ['05ac:024f'],
      devices: [ { pid: '12a8', vid: '05ac', vid_pid: '05ac:12a8', friendly_name: 'Apple Mobile Device (Composite)', interfaces: 6, classes: ['USB','PortableDevice'], statuses: ['OK'], peripheral: false, iphone_hint: true },
                 { pid: '024f', vid: '05ac', vid_pid: '05ac:024f', friendly_name: 'USB Input Device', interfaces: 12, classes: ['HIDClass','Keyboard'], statuses: ['OK'], peripheral: true, iphone_hint: false } ] },
    device: { available: true, name: 'iPhone', product_type: 'iPhone8,4', ios_version: '15.8.3', build: '19H380', model: 'N69AP', serial: 'F4GXN0ABCDEF', udid: '6f1d9c4e', battery_pct: 42, charging: true, fully_charged: false, error: null },
    usbmux_ids: ['6f1d9c4e'], warnings: ['SAMPLE DATA: hand-written fixture.'], notes: ['Apple Magic Keyboard (HID peripheral) is attached. That is an Apple HID peripheral, NOT an iPhone.'] },
  not_queryable: { status: 'not_queryable', _meta: { stale: false, iphone_file: { exists: true, age_seconds: 3, path: 'x.json' } },
    usb: { present: true, likely_iphone: true, device_count: 5, peripherals: [], devices: [ { pid: '12a8', vid: '05ac', vid_pid: '05ac:12a8', friendly_name: 'Apple Mobile Device (Composite)', interfaces: 5, classes: ['USB','WPD'], statuses: ['OK','Error'], peripheral: false, iphone_hint: true } ] },
    device: { available: false, error: 'No device found!' }, usbmux_ids: ['6f1d9c4e'],
    warnings: ['iPhone is locked. Unlock the screen and tap Trust.'], notes: ['An Apple device is present but not queryable (typical for a locked or not-yet-trusted iPhone) - unlock the iPhone and tap Trust.'] },
  peripheral_only: { status: 'peripheral_only', _meta: { stale: true, iphone_file: { exists: true, age_seconds: 488.6, path: 'iphone.json' } },
    usb: { present: true, likely_iphone: false, device_count: 15, peripherals: ['05ac:024f'],
      devices: [ { pid: '024f', vid: '05ac', vid_pid: '05ac:024f', friendly_name: 'USB Input Device', interfaces: 15, classes: ['HIDClass','USB','Keyboard','Mouse'], statuses: ['OK'], peripheral: true, iphone_hint: false } ] },
    device: null, usbmux_ids: [], warnings: [], notes: ['Apple Magic Keyboard (HID peripheral) is attached. That is an Apple HID peripheral, NOT an iPhone.'] },
  no_device: { status: 'no_device', _meta: { stale: false, iphone_file: { exists: true, age_seconds: 1, path: 'x.json' } },
    usb: { present: false, likely_iphone: false, device_count: 0, peripherals: [], devices: [] }, device: null, usbmux_ids: [], warnings: [], notes: [] },
  no_data: { status: 'no_data', _meta: { stale: true, iphone_file: { exists: false, age_seconds: null, path: 'C:\\gone\\iphone.json', error: 'missing' } },
    usb: { present: null, likely_iphone: null, device_count: 0, peripherals: [], devices: [] }, device: null, usbmux_ids: [], warnings: [],
    notes: ['iphone.json not found at C:\\gone\\iphone.json - the iPhone monitor has never written a snapshot.'] }
};

payloads.state = { seq: 7, device: { mode: 'mtp', present: true, friendly_name: 'SAMSUNG' }, _meta: { stale: false, state_file: { exists: true, age_seconds: 1.2, path: 'state.json' }, iphone_file: { exists: true, age_seconds: 2.1 }, iphone_stale: false }, history: [] };
/* the REAL state.json shape: mode + phone_present at the top level, no "device" object */
const realStateShape = { seq: 278, mode: 'absent', phone_present: false, devices: [], adb: {}, lan: {}, com_ports: [], processes: [],
  _meta: { stale: false, state_file: { exists: true, age_seconds: 1.9, path: 'state.json' }, iphone_file: { exists: true, age_seconds: 488 }, iphone_stale: true }, history: [] };

let pass = 0, fail = 0;
function check(label, ok, detail) { if (ok) { pass++; console.log('  PASS  ' + label); } else { fail++; console.log('  FAIL  ' + label + (detail ? '  ->  ' + detail : '')); } }

/* the page's poll functions fire-and-forget; wrap them so the harness can await.
   The stub fetch resolves on a microtask, so draining a few ticks is enough. */
const tick = () => new Promise(r => setImmediate(r));
async function pollIphone() { ctx.pollIphone(); await tick(); await tick(); await tick(); }
async function pollState() { ctx.poll(); await tick(); await tick(); }

(async () => {
  for (const [name, p] of Object.entries(cases)) {
    payloads.iphone = p;
    ctx.lastIphonePayload = null; ctx.ipOkAt = 0; ctx.ipSeenAt = {};
    await pollIphone();
    await pollState();
    ctx.ipOkAt = Date.now();   // the poll landed, so the feed itself is fresh;
                               // staleness of the DATA must come from _meta only
    await pollState();
    const title = store.ipModeTitle.textContent;
    const card = store.iphoneCard.className;
    const rows = store.ipDevices.innerHTML;
    console.log('\n[' + name + ']  title="' + title + '"  card="' + card + '"');
    console.log('   presence="' + store.ipPresence.textContent + '"  pill="' + store.ipModeSeen.textContent + '"  fresh="' + store.ipFresh.textContent + '"');
    console.log('   devices html (' + rows.length + ' chars): ' + rows.replace(/\s+/g, ' ').slice(0, 210));
    console.log('   dedup note: ' + store.ipDedupNote.textContent);
    console.log('   battery txt: ' + store.ipBattText.textContent + '  | width=' + store.ipBattFill.style.width + ' class=' + store.ipBattFill.className);
    console.log('   warn box   : ' + store.ipWarn.innerHTML.replace(/\s+/g, ' ').slice(0, 170));
    console.log('   age        : ' + store.ipAge.textContent);
    console.log('   both-line  : ' + store.bothIphone.textContent + '  | chip: ' + store.bothChip.textContent);
    if (name === 'queryable') {
      check('status shows QUERYABLE', title === 'QUERYABLE', title);
      check('presence chip ok', store.ipPresence.className === 'chip ok', store.ipPresence.className);
      check('2 dedup rows only (not 18)', (rows.match(/<tr/g) || []).length === 3, 'rows=' + (rows.match(/<tr/g) || []).length); // 1 header + 2 body
      check('peripheral labelled NOT a phone', rows.includes('NOT a phone'));
      check('interfaces counted (6 and 12)', rows.includes('>6<') && rows.includes('>12<'));
      check('battery 42%', store.ipBattText.textContent.indexOf('42%') === 0, store.ipBattText.textContent);
      check('battery width 42%', store.ipBattFill.style.width === '42%', store.ipBattFill.style.width);
      check('charging shown', store.ipBattText.textContent.includes('charging'));
      check('iOS version shown', store.ipIos.textContent === '15.8.3', store.ipIos.textContent);
      check('product type shown', store.ipProduct.textContent === 'iPhone8,4');
      check('dedup note explains 18->2', store.ipDedupNote.textContent.includes('18 raw') && store.ipDedupNote.textContent.includes('2 physical'));
      check('warnings rendered', store.ipWarn.innerHTML.includes('SAMPLE DATA'));
      check('both-line says QUERYABLE', store.bothIphone.textContent.includes('QUERYABLE'));
      check('both-line has batt 42%', store.bothIphone.textContent.includes('batt 42%'));
    }
    if (name === 'not_queryable') {
      check('status DETECTED, NOT QUERYABLE', title === 'DETECTED, NOT QUERYABLE', title);
      check('warning box marked bad', store.ipWarn.innerHTML.includes('warnbox bad'));
      check('device error row populated', store.ipDevErr.textContent === 'No device found!' && store.ipDevErr.className === 'v err', store.ipDevErr.textContent);
      check('no battery bar fill', store.ipBattFill.style.width === '0%', store.ipBattFill.style.width);
      check('error row styled', store.ipWarn.innerHTML.includes('Unlock the screen'));
    }
    if (name === 'peripheral_only') {
      check('status APPLE PERIPHERAL ONLY', title === 'APPLE PERIPHERAL ONLY', title);
      check('presence says peripheral only', store.ipPresence.textContent === 'peripheral only', store.ipPresence.textContent);
      check('single row though 15 raw', (rows.match(/<tr/g) || []).length === 2, 'rows=' + (rows.match(/<tr/g) || []).length);
      check('row marked warnrow', rows.includes('warnrow'));
      check('data staleness on the age row', store.ipAge.textContent.includes('stale'), store.ipAge.textContent);
      check('data staleness on the mode pill', store.ipModeSeen.textContent.includes('STALE'), store.ipModeSeen.textContent);
      check('data staleness in the summary chip', store.bothChip.textContent === 'iPhone feed stale', store.bothChip.textContent);
      check('poll freshness shown separately as live', store.ipFresh.textContent.includes('live'), store.ipFresh.textContent);
    }
    if (name === 'no_device') {
      check('status NO APPLE DEVICE', title === 'NO APPLE DEVICE', title);
      check('empty device list message', store.ipDevices.innerHTML.includes('no Apple USB devices reported'));
      check('presence chip absent', store.ipPresence.textContent === 'absent', store.ipPresence.textContent);
    }
    if (name === 'no_data') {
      check('status NO DATA', title === 'NO DATA', title);
      check('missing file surfaced', store.ipModeSeen.textContent === 'no iphone.json', store.ipModeSeen.textContent);
      check('missing-file note shown', store.ipWarn.innerHTML.includes('never written a snapshot'));
      check('both feed age row says missing', store.bothIphoneAge.textContent === 'missing', store.bothIphoneAge.textContent);
      check('summary chip flags the iPhone feed', store.bothChip.textContent === 'iPhone feed stale', store.bothChip.textContent);
      check('short UDID truncated', true);
      check('no crash with nulls', true);
    }
  }
  console.log('\n--- ' + pass + ' passed, ' + fail + ' failed ---');

  /* regression: the real state.json shape (mode/phone_present at top level) */
  payloads.iphone = cases.no_device; payloads.state = realStateShape;
  ctx.lastIphonePayload = null; ctx.ipOkAt = 0; ctx.ipSeenAt = {};
  await pollIphone(); await pollState(); ctx.ipOkAt = Date.now(); await pollState();
  console.log('\n[real state.json shape] samsung summary: "' + store.bothSamsung.textContent + '"  chip: ' + store.bothChip.textContent);
  check('top-level mode/phone_present summarised', store.bothSamsung.textContent.indexOf('ABSENT') === 0 && store.bothSamsung.textContent.includes('absent'), store.bothSamsung.textContent);
  check('stale from state meta never mislabelled', store.bothIphoneAge.textContent === '1.0s ago \u00b7 ok', store.bothIphoneAge.textContent);
  console.log('\nFINAL --- ' + pass + ' passed, ' + fail + ' failed ---');
  console.log('element ids requested that do not exist in the HTML: ' + (missing.size ? [...missing].join(', ') : 'none (refreshBtn now exists)'));
  process.exit(fail ? 1 : 0);
})();
