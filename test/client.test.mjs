import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';
import test from 'node:test';

function load(extra = {}) {
  let bundle;
  vm.runInNewContext(readFileSync(new URL('../lib/client.js', import.meta.url), 'utf8'), {
    window: { __ModuleLoader__: { load: value => { bundle = value; } } },
    AbortController, setTimeout, clearTimeout, setInterval, clearInterval, crypto: webcrypto,
    ...extra,
  });
  assert.equal(bundle.id, 'mimi-desktop-pet');
  return bundle.factory(name => { assert.equal(name, 'react'); return {}; });
}

const success = value => ({ ok: true, value });

test('client actions use DSH Connection and preserve error text after refreshing', async () => {
  const calls = [];
  const { createPanelModel } = load();
  const model = createPanelModel(async (channel, endpoint, payload) => {
    calls.push({ channel, endpoint, payload });
    return endpoint === 'mimi-pet/state' ? success({ available: true, enabled: true, snapshot: { revision: 3 } })
      : { ok: false, error: { message: '请填写将来的时间' } };
  });
  await model.refresh();
  assert.equal(model.getSnapshot().available, true);
  assert.equal(await model.act({ op: 'reminder.add', text: '喝水', due_at: 1 }), false);
  assert.equal(model.getSnapshot().actionError, '请填写将来的时间');
  assert.equal(model.getSnapshot().busy, false);
  assert.ok(calls.every(call => call.channel === '/api'));
  assert.match(calls.find(call => call.endpoint === 'mimi-pet/action').payload.id, /^[\w-]+$/);
  model.dispose();
});

test('a delayed poll cannot overwrite an acknowledged action', async () => {
  const { createPanelModel } = load();
  let finishPoll;
  const model = createPanelModel(async (_channel, endpoint) => endpoint === 'mimi-pet/state'
    ? new Promise(resolve => { finishPoll = resolve; })
    : success({ message: '已开始', state: { available: true, snapshot: { revision: 2 } } }));
  const poll = model.refresh();
  await model.act({ op: 'focus.start', minutes: 25 });
  finishPoll(success({ available: true, snapshot: { revision: 1 } }));
  await poll;
  assert.equal(model.getSnapshot().snapshot.revision, 2);
  model.dispose();
});

test('plugin disposal aborts in-flight RPC and ignores late results', async () => {
  const { createPanelModel } = load();
  let signal, finish;
  const model = createPanelModel((_channel, _endpoint, _payload, nextSignal) => {
    signal = nextSignal;
    return new Promise(resolve => { finish = resolve; });
  });
  const poll = model.refresh();
  model.dispose();
  assert.equal(signal.aborted, true);
  finish(success({ available: true }));
  await poll;
  assert.equal(model.getSnapshot().available, false);
});

test('the client contributes an embedded sidebar tab and header button with scoped cleanup', async () => {
  const registrations = [];
  const cleanups = [];
  const removals = [];
  const document = {
    visibilityState: 'visible',
    createElement() { return { dataset: {}, remove() { removals.push('style'); } }; },
    head: { appendChild() {} }, addEventListener() {}, removeEventListener() {},
  };
  const client = load({ document });
  const ctx = {
    connection: { rpc: { call: async () => success({ available: false }) } },
    effect(factory) { const disposer = factory(); cleanups.push(disposer); return disposer; },
    sidebarRight: { openTab() {} },
    sidebarRightTabs: { register(value) { registrations.push(value); return () => removals.push('tab'); } },
    slots: {
      inject(_name, factory) { return factory(); },
      register(value, component) { registrations.push({ ...value, component }); return () => removals.push(value.name); },
    },
  };
  client.apply(ctx);
  assert.ok(registrations.some(item => item.kind === 'mimi-companion'));
  assert.ok(registrations.some(item => item.name === 'sidebar.right.pane.tab' && item.key === 'mimi-desktop-pet'));
  assert.ok(registrations.some(item => item.name === 'conversation.session.header.actions'));
  for (const cleanup of cleanups.reverse()) cleanup();
  assert.equal(removals.length, 4);
});
