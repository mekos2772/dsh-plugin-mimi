import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import { join } from 'node:path';
import test from 'node:test';
import { PetRuntime, resolvePetPaths } from '../lib/pet-runtime.js';

const PREFIX = 'MIMI/1 ';
const snapshot = (extra = {}) => ({ enabled: true, default_minutes: 25, quiet: false,
  focus: null, reminders: [], notices: [], revision: 0, ...extra });

function fixture(options = {}) {
  let clock = 0;
  let serial = 0;
  const timers = new Map();
  const frames = [];
  const launches = [];
  const children = [];
  const cfg = { enabled: true, companionEnabled: true, focusMinutes: 40, petDir: 'pet-root', ...options.config };
  const runtime = new PetRuntime({
    getConfig: () => cfg, packageDir: 'package-root', getAuthUrl: options.getAuthUrl || (() => 'http://127.0.0.1:3080/?token=test-only'),
    env: {}, exists: () => true, now: () => clock,
    setTimer(callback, delay) { const id = ++serial; timers.set(id, { callback, at: clock + delay }); return id; },
    clearTimer(id) { timers.delete(id); },
    spawnProcess(...args) {
      launches.push(args);
      const child = new EventEmitter();
      child.pid = 123;
      child.stdout = new PassThrough();
      child.stdin = new Writable({ write(chunk, encoding, done) {
        const frame = JSON.parse(String(chunk).slice(PREFIX.length));
        frames.push(frame);
        if (frame.type === 'shutdown' && options.graceful !== false) queueMicrotask(() => child.emit('exit', 0));
        done();
      } });
      child.kill = () => { child.killed = true; child.emit('exit', null, 'SIGTERM'); };
      children.push(child);
      return child;
    },
  });
  return { runtime, cfg, frames, launches, children, timers,
    ready(extra = {}) {
      runtime.start();
      children.at(-1).stdout.write(PREFIX + JSON.stringify({ type: 'ready', sequence: 0, protocol: 1, snapshot: snapshot(extra) }) + '\n');
    },
    advance(ms) {
      const end = clock + ms;
      while (true) {
        const next = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        const [id, timer] = next;
        clock = timer.at; timers.delete(id); timer.callback();
      }
      clock = end;
    },
  };
}

test('installed runtime wins over automatic developer-folder discovery', () => {
  const exists = () => true;
  assert.equal(resolvePetPaths({}, 'package-root', { USERPROFILE: 'home' }, exists).root, join('package-root', 'pet'));
  assert.equal(resolvePetPaths({ petDir: 'custom' }, 'package-root', {}, exists).root, 'custom');
  assert.throws(() => resolvePetPaths({ petDir: 'missing' }, 'package-root', {}, () => false), /运行文件/);
});

test('DSH starts exactly one hidden child with companion settings and pipes', async () => {
  const f = fixture();
  f.ready(); f.runtime.start();
  assert.equal(f.launches.length, 1);
  const [, args, options] = f.launches[0];
  assert.deepEqual(args, ['-m', 'mimi_pet.main']);
  assert.deepEqual(options.stdio, ['pipe', 'pipe', 'ignore']);
  assert.equal(options.windowsHide, true);
  assert.equal(options.env.MIMI_FOCUS_MINUTES, '40');
  assert.equal(options.env.MIMI_COMPANION_IPC, 'stdio-v1');
  assert.equal(f.runtime.state().available, true);
  await f.runtime.dispose();
  assert.equal(f.frames.at(-1).type, 'shutdown');
  assert.equal(f.children[0].killed, undefined);
});

test('disabled pet never spawns; disabled companion is propagated', async () => {
  const disabled = fixture({ config: { enabled: false } });
  disabled.runtime.start();
  assert.equal(disabled.launches.length, 0);
  assert.equal(disabled.runtime.state().phase, 'disabled');
  const f = fixture({ config: { companionEnabled: false } });
  f.ready();
  assert.equal(f.launches[0][2].env.MIMI_COMPANION_ENABLED, '0');
  const result = await f.runtime.request('action', { id: 'request-01', action: { op: 'focus.start' } });
  assert.equal(result.ok, false);
  await f.runtime.dispose();
});

test('disposing while waiting for authentication cancels every start retry', async () => {
  let authenticated = false;
  const f = fixture({ getAuthUrl: () => authenticated ? 'test-auth' : '' });
  f.runtime.scheduleStart();
  f.advance(1500);
  assert.equal(f.launches.length, 0);
  assert.equal(f.timers.size, 1);
  await f.runtime.dispose();
  authenticated = true;
  f.advance(5000);
  assert.equal(f.launches.length, 0);
  assert.equal(f.timers.size, 0);
});

test('action waits for the child result; request replay is idempotent', async () => {
  const f = fixture(); f.ready();
  const payload = { id: 'request-reminder', action: { op: 'reminder.add', text: '喝水', due_at: 1800000600 } };
  const first = f.runtime.request('action', payload);
  const duplicate = f.runtime.request('action', payload);
  assert.equal(f.frames.length, 1);
  f.runtime.receive({ type: 'result', sequence: 1, id: payload.id, result: { ok: true, message: '已添加' }, snapshot: snapshot({ revision: 1 }) });
  assert.deepEqual(await first, await duplicate);
  assert.equal((await first).value.state.snapshot.revision, 1);
  assert.deepEqual(await f.runtime.request('action', payload), await first);
  assert.equal(f.frames.length, 1);
  assert.equal((await f.runtime.request('action', { ...payload, action: { op: 'notices.clear' } })).error.code, 'mimi/duplicate-id');
  await f.runtime.dispose();
});

test('invalid operations, aborted requests and stale runtimes cannot execute', async () => {
  const f = fixture(); f.ready();
  assert.equal((await f.runtime.request('action', { id: 'request-invalid', action: { op: 'exec' } })).ok, false);
  const aborted = new AbortController(); aborted.abort();
  assert.equal((await f.runtime.request('action', {}, aborted.signal)).error.code, 'mimi/cancelled');
  f.advance(5100);
  assert.equal(f.runtime.state().available, false);
  assert.equal((await f.runtime.request('action', { id: 'request-stale', action: { op: 'focus.start' } })).ok, false);
  assert.equal(f.frames.length, 0);
  await f.runtime.dispose();
});

test('a timed-out request is never resent automatically', async () => {
  const f = fixture(); f.ready();
  const payload = { id: 'request-timeout', action: { op: 'focus.start' } };
  const result = f.runtime.request('action', payload);
  f.advance(8000);
  assert.equal((await result).error.code, 'mimi/timeout');
  assert.equal((await f.runtime.request('action', payload)).error.code, 'mimi/timeout');
  assert.equal(f.frames.length, 1);
  await f.runtime.dispose();
});

test('split UTF-8 frames preserve Chinese and navigation has no window side effect', async () => {
  const f = fixture(); f.runtime.start();
  const bytes = Buffer.from(PREFIX + JSON.stringify({ type: 'state', sequence: 1, snapshot: snapshot({ status_text: '专注陪伴' }) }) + '\n');
  const split = bytes.indexOf(Buffer.from('专')) + 1;
  f.children[0].stdout.write(bytes.subarray(0, split));
  f.children[0].stdout.write(bytes.subarray(split));
  assert.equal(f.runtime.state().snapshot.status_text, '专注陪伴');
  f.runtime.receive({ type: 'state', sequence: 0, snapshot: snapshot({ status_text: '过期状态' }) });
  assert.equal(f.runtime.state().snapshot.status_text, '专注陪伴');
  f.runtime.receive({ type: 'navigate', section: 'notices' });
  assert.deepEqual(f.runtime.state().navigation, { revision: 1, section: 'notices' });
  assert.equal(f.launches.length, 1);
  await f.runtime.dispose();
});

test('an unresponsive owned child is reaped after the graceful deadline', async () => {
  const f = fixture({ graceful: false }); f.ready();
  const pending = f.runtime.request('action', { id: 'request-pending', action: { op: 'focus.start' } });
  const stopping = f.runtime.dispose();
  assert.equal((await pending).error.code, 'mimi/stopped');
  f.advance(1800);
  await stopping;
  assert.equal(f.children[0].killed, true);
  assert.equal(f.runtime.state().available, false);
  assert.equal(f.timers.size, 0);
});

test('asynchronous spawn failure reports a recoverable state', async () => {
  const f = fixture(); f.runtime.start();
  f.children[0].emit('error', new Error('ENOENT'));
  assert.equal(f.runtime.state().phase, 'error');
  assert.equal(f.runtime.child, null);
  await f.runtime.dispose();
});
