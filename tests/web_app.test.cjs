const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');

function client(fetch) {
  const context = vm.createContext({
    fetch, console, setTimeout,
    document: { getElementById: () => ({}), addEventListener: () => {} },
    window: { addEventListener: () => {} }, location: { hash: '' },
  });
  const code = readFileSync(resolve(__dirname, '../web/app.js'), 'utf8').replace(/render\(\);\s*$/, '');
  vm.runInContext(code + '\nrender = async () => {};', context);
  return context;
}

test('double click cannot start a second paid request', async () => {
  let release;
  let calls = 0;
  const ctx = client(async () => {
    calls++;
    await new Promise(resolve => { release = resolve; });
    return { ok: true, json: async () => ({ text: 'Saved' }) };
  });
  vm.runInContext('state.profileId = "E1";', ctx);
  const first = vm.runInContext('actions.explain({dataset: {event: "EV1"}})', ctx);
  await new Promise(resolve => setImmediate(resolve));
  await vm.runInContext('actions.explain({dataset: {event: "EV1"}})', ctx);
  assert.equal(calls, 1);
  release();
  await first;
});

test('late explanation never appears on a different profile', async () => {
  let release;
  const ctx = client(async () => {
    await new Promise(resolve => { release = resolve; });
    return { ok: true, json: async () => ({ text: 'Private facts for E1' }) };
  });
  vm.runInContext('state.profileId = "E1";', ctx);
  const request = vm.runInContext('actions.explain({dataset: {event: "EV1"}})', ctx);
  await new Promise(resolve => setImmediate(resolve));
  vm.runInContext('state.profileId = "E2";', ctx);
  release();
  await request;
  assert.equal(vm.runInContext('Object.keys(state.explanations).length', ctx), 0);
});

test('assistant response is displayed and draft is cleared after success', async () => {
  const ctx = client(async () => ({ ok: true, json: async () => ({ text: 'Skills based on your profile', suggestion: null }) }));
  vm.runInContext('state.profileId = "E1"; state.wish = "Skills?";', ctx);
  await vm.runInContext('forms.coach({wish: {value: "Skills?"}})', ctx);
  assert.equal(vm.runInContext('state.dialogue[1].text', ctx), 'Skills based on your profile');
  assert.equal(vm.runInContext('state.wish', ctx), '');
});
