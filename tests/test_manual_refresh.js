/* Offline checks: opening a dashboard must never trigger a Salesforce refresh. */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync('docs/app.js', 'utf8').replace(/load\(\);\s*$/, '');
async function checkLoad(ok, config) {
  const calls = [];
  const elements = new Map();
  const context = { document: { querySelectorAll: () => [], getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, { className: '', textContent: '' });
    return elements.get(id);
  } }, fetch: async (path, options) => {
    calls.push({ path, options });
    return { ok, status: ok ? 200 : 404, json: async () => ({ generatedAt: '2000-01-01T00:00:00Z', config, rows: [] }) };
  } };
  vm.createContext(context);
  vm.runInContext(source, context);
  vm.runInContext('wire = () => {}; hydrate = () => {}; detectApi = async () => { state.writeApi.refreshEnabled = true; state.writeApi.loggedIn = true; };', context);
  await vm.runInContext('load()', context);
  assert.equal(calls.length, 1);
  assert(calls[0].path.startsWith('pse-data.json'));
  assert(!calls.some(({ path, options }) => path === '/api/refresh' || options?.method === 'POST'));
  if (!ok) assert(elements.get('status').textContent.includes('Click Refresh'));
}
(async () => {
  await checkLoad(true, { scopeVersion: 4 });
  await checkLoad(true, { scopeVersion: 1 });
  await checkLoad(false, {});
  const requests = [];
  const elements = new Map();
  const context = { document: { querySelectorAll: () => [], getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, {});
    return elements.get(id);
  } }, requests };
  vm.createContext(context);
  vm.runInContext(source, context);
  vm.runInContext('renderUpdate = () => {}; ensureWriteLogin = async () => true; reloadSnapshot = async () => {}; postJson = async (path, body) => { requests.push({path, body}); return {summary:{total:2}}; };', context);
  await vm.runInContext('refreshData()', context);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, '/api/refresh');
  assert.equal(requests[0].body.manual, true);
  assert.equal(elements.get('refreshBtn').textContent, 'Refresh');
  console.log('Manual refresh checks passed: old/missing/incompatible snapshots do not refresh on load; Refresh explicitly requests data.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
