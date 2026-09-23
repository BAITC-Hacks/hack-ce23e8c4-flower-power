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
  const first = vm.runInContext('forms.chat({dataset: {event: "EV1"}, wish: {value: "Why?"}})', ctx);
  await new Promise(resolve => setImmediate(resolve));
  await vm.runInContext('forms.chat({dataset: {event: "EV1"}, wish: {value: "Why?"}})', ctx);
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
  const request = vm.runInContext('forms.chat({dataset: {event: "EV1"}, wish: {value: "Why?"}})', ctx);
  await new Promise(resolve => setImmediate(resolve));
  vm.runInContext('state.profileId = "E2";', ctx);
  release();
  await request;
  assert.equal(vm.runInContext('activityChat("EV1").messages.length', ctx), 0);
});

test('assistant response is displayed and draft is cleared after success', async () => {
  const ctx = client(async () => ({ ok: true, json: async () => ({ text: 'Skills based on your profile', suggestion: null }) }));
  vm.runInContext('state.profileId = "E1"; activityChat("EV1").draft = "Skills?";', ctx);
  await vm.runInContext('forms.chat({dataset: {event: "EV1"}, wish: {value: "Skills?"}})', ctx);
  assert.equal(vm.runInContext('activityChat("EV1").messages[1].text', ctx), 'Skills based on your profile');
  assert.equal(vm.runInContext('activityChat("EV1").draft', ctx), '');
});

test('opening and closing a card chat is free and retains its draft', () => {
  const ctx = client(() => { throw new Error('Opening a chat must not call API'); });
  vm.runInContext('state.profileId = "E1"; actions["toggle-chat"]({dataset:{event:"EV1"}}); activityChat("EV1").draft = "Why?";', ctx);
  assert.equal(vm.runInContext('activityChat("EV1").open', ctx), true);
  vm.runInContext('actions["toggle-chat"]({dataset:{event:"EV1"}}); actions["toggle-chat"]({dataset:{event:"EV1"}});', ctx);
  assert.equal(vm.runInContext('activityChat("EV1").draft', ctx), 'Why?');
  assert.equal(vm.runInContext('activityChat("EV2").open', ctx), false);
});

test('each card sends its event and keeps its own conversation', async () => {
  const events = [];
  const ctx = client(async (_url, init) => {
    const data = JSON.parse(init.body);
    events.push(data.event_id);
    return { ok: true, json: async () => ({ text: `Answer about ${data.event_id}`, details: 'Facts' }) };
  });
  vm.runInContext('state.profileId = "E1";', ctx);
  await vm.runInContext('forms.chat({dataset:{event:"EV1"}, wish:{value:"Why?"}})', ctx);
  await vm.runInContext('forms.chat({dataset:{event:"EV2"}, wish:{value:"Why?"}})', ctx);
  assert.deepEqual(events, ['EV1', 'EV2']);
  assert.equal(vm.runInContext('activityChat("EV1").messages[1].text', ctx), 'Answer about EV1');
  assert.equal(vm.runInContext('activityChat("EV2").messages[1].text', ctx), 'Answer about EV2');
});
