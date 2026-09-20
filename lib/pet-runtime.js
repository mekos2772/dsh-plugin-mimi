/** DSH-owned pet process and its private stdio companion channel. */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { StringDecoder } from 'node:string_decoder';
import { performance } from 'node:perf_hooks';

export const CHANNEL = '/api';
const PREFIX = 'MIMI/1 ';
const MAX_LINE = 1024 * 1024;
const OPS = new Set([
  'focus.start', 'focus.break', 'focus.pause', 'focus.resume', 'focus.cancel',
  'focus.dismiss', 'quiet.set', 'reminder.add', 'reminder.update',
  'reminder.snooze', 'notices.clear', 'command',
]);

const failure = (code, message) => ({ ok: false, error: { code: `mimi/${code}`, message, details: {} } });
const success = value => ({ ok: true, value });

export function resolvePython(env = process.env, exists = existsSync) {
  const local = env.LOCALAPPDATA || '';
  return [
    join(local, 'Programs', 'Python', 'Python311', 'pythonw.exe'),
    join(local, 'Programs', 'Python', 'pythonw.exe'),
    'C:\\Windows\\pyw.exe',
  ].find(exists) || 'pythonw';
}

/** Prefer the installed runtime; a developer checkout must be selected explicitly. */
export function resolvePetPaths(cfg, packageDir, env = process.env, exists = existsSync) {
  let root = cfg.petDir || env.MIMI_PET_DIR || '';
  if (!root) {
    const home = env.USERPROFILE || '';
    root = [
      join(packageDir, 'pet'),
      join(home, 'Desktop', '桌宠'),
      join(home, 'Desktop', 'mimi-desktop-pet'),
      join(home, 'mimi-desktop-pet'),
    ].find(path => exists(join(path, 'mimi_app', 'src'))) || '';
  }
  if (!root || !exists(join(root, 'mimi_app', 'src'))) {
    throw new Error('未找到桌宠运行文件。请检查 DSH 设置中的 mimi-pet.petDir 或重新安装 Mimi 插件。');
  }
  if (!exists(join(root, 'assets', 'characters', 'mimi', 'library_v1', 'manifest.json'))) {
    throw new Error('桌宠动作素材缺失，请重新安装 Mimi 插件。');
  }
  return { root, appDir: join(root, 'mimi_app') };
}

export class PetRuntime {
  constructor({ getConfig, packageDir, getAuthUrl, log = () => {}, env = process.env,
    spawnProcess = spawn, exists = existsSync, now = () => performance.now(),
    setTimer = setTimeout, clearTimer = clearTimeout, actionTimeoutMs = 8000, shutdownMs = 1800 }) {
    Object.assign(this, { getConfig, packageDir, getAuthUrl, log, env, spawnProcess, exists,
      now, setTimer, clearTimer, actionTimeoutMs, shutdownMs });
    this.child = null;
    this.snapshot = null;
    this.phase = 'stopped';
    this.message = 'Mimi 尚未启动。';
    this.lastStateAt = -Infinity;
    this.lastSequence = -1;
    this.navigation = { revision: 0, section: 'reminders' };
    this.pending = new Map();
    this.completed = new Map();
    this.startTimer = null;
    this.disposed = false;
    this.stopping = null;
  }

  state() {
    const cfg = this.getConfig();
    const fresh = this.child !== null && this.now() - this.lastStateAt < 5000;
    const available = !this.disposed && !this.stopping && this.phase === 'running' && fresh;
    return {
      enabled: cfg.companionEnabled !== false,
      pet_enabled: cfg.enabled !== false,
      default_minutes: cfg.focusMinutes || 25,
      phase: this.phase,
      available,
      message: this.phase === 'running' && !fresh ? '正在等待 Mimi 同步状态…' : this.message,
      snapshot: this.snapshot,
      navigation: this.navigation,
    };
  }

  scheduleStart(delay = 1500) {
    if (this.disposed || this.child || this.startTimer !== null) return;
    this.startTimer = this.setTimer(() => {
      this.startTimer = null;
      this.start();
    }, delay);
    this.startTimer?.unref?.();
  }

  start() {
    if (this.disposed || this.child || this.stopping) return this.state();
    if (this.startTimer !== null) this.clearTimer(this.startTimer);
    this.startTimer = null;
    const cfg = this.getConfig();
    if (cfg.enabled === false) {
      this.phase = 'disabled';
      this.message = '请在 DSH 的 mimi-pet 设置中启用桌宠，再重启插件。';
      return this.state();
    }
    let authUrl;
    try { authUrl = this.getAuthUrl(); } catch { /* wait for Connection */ }
    if (!authUrl) {
      this.phase = 'starting';
      this.message = '正在等待 DSH 连接…';
      this.scheduleStart(1000);
      return this.state();
    }
    try {
      const { root, appDir } = resolvePetPaths(cfg, this.packageDir, this.env, this.exists);
      const minutes = Number.isInteger(cfg.focusMinutes) && cfg.focusMinutes >= 1 && cfg.focusMinutes <= 240
        ? cfg.focusMinutes : 25;
      const launched = this.spawnProcess(cfg.python || resolvePython(this.env, this.exists), ['-m', 'mimi_pet.main'], {
        cwd: appDir,
        env: {
          ...this.env,
          PYTHONPATH: join(appDir, 'src'),
          PYTHONIOENCODING: 'utf-8',
          MIMI_ASSET_ROOT: root,
          MIMI_SCALE_PERCENT: String(Math.max(1, Math.min(200, Number(cfg.scale) || 100))),
          MIMI_DSH_AUTH_URL: authUrl,
          MIMI_DSH_HOST: new URL(authUrl).host,
          MIMI_COMPANION_IPC: 'stdio-v1',
          MIMI_COMPANION_ENABLED: cfg.companionEnabled === false ? '0' : '1',
          MIMI_FOCUS_MINUTES: String(minutes),
        },
        windowsHide: true,
        stdio: ['pipe', 'pipe', 'ignore'],
      });
      this.child = launched;
      this.phase = 'starting';
      this.message = 'Mimi 正在启动…';
      this.snapshot = null;
      this.lastStateAt = -Infinity;
      this.lastSequence = -1;
      this.completed.clear();
      const decoder = new StringDecoder('utf8');
      let buffer = '';
      launched.stdout.on('data', data => {
        if (this.child !== launched) return;
        buffer += decoder.write(data);
        let newline;
        while ((newline = buffer.indexOf('\n')) !== -1) {
          const line = buffer.slice(0, newline).trimEnd();
          buffer = buffer.slice(newline + 1);
          if (line.length > MAX_LINE) continue;
          if (!line.startsWith(PREFIX)) continue;
          try { this.receive(JSON.parse(line.slice(PREFIX.length))); } catch { /* ignore invalid protocol frames */ }
        }
        if (buffer.length > MAX_LINE) buffer = '';
      });
      // Pipe errors and asynchronous spawn errors must never take down DSH.
      launched.stdin.on('error', () => {
        if (this.child === launched && !this.stopping) this.markFailed('Mimi 通信已断开，请重新启动插件。');
      });
      launched.stdout.on('error', () => {});
      launched.once('error', () => {
        if (this.child !== launched) return;
        this.child = null;
        this.markFailed('Mimi 启动失败，请检查 Python 与 PySide6 配置。');
      });
      launched.once('exit', () => {
        if (this.child !== launched) return;
        this.child = null;
        this.phase = 'stopped';
        this.message = 'Mimi 已停止。';
        this.lastStateAt = -Infinity;
        this.rejectPending('stopped', 'Mimi 已退出，未确认的操作请先核对状态。');
      });
      this.log(`Mimi 已启动 pid=${launched.pid}`);
    } catch (error) {
      this.child = null;
      this.markFailed(error.message || 'Mimi 启动失败。');
    }
    return this.state();
  }

  markFailed(message) {
    this.phase = 'error';
    this.message = message;
    this.lastStateAt = -Infinity;
    this.rejectPending('unavailable', message);
    this.log(message);
  }

  receive(frame) {
    if (!frame || typeof frame !== 'object' || this.disposed) return;
    if ((frame.type === 'state' || frame.type === 'ready' || frame.type === 'result')
        && Number.isSafeInteger(frame.sequence) && frame.sequence > this.lastSequence
        && frame.snapshot && Array.isArray(frame.snapshot.reminders) && Array.isArray(frame.snapshot.notices)) {
      this.snapshot = frame.snapshot;
      this.lastSequence = frame.sequence;
      this.lastStateAt = this.now();
      if (!this.stopping) {
        this.phase = 'running';
        this.message = '';
      }
    }
    if (frame.type === 'navigate') {
      this.navigation = {
        revision: this.navigation.revision + 1,
        section: frame.section === 'notices' ? 'notices' : 'reminders',
      };
    }
    if (frame.type === 'result' && this.pending.has(frame.id)) {
      const item = this.pending.get(frame.id);
      this.pending.delete(frame.id);
      this.clearTimer(item.timer);
      const result = frame.result?.ok
        ? success({ ...frame.result, state: this.state() })
        : failure('invalid-action', frame.result?.message || '操作未完成。');
      this.remember(frame.id, item.fingerprint, result);
      item.resolve(result);
    }
  }

  remember(id, fingerprint, result) {
    this.completed.set(id, { fingerprint, result });
    while (this.completed.size > 128) this.completed.delete(this.completed.keys().next().value);
  }

  send(frame) {
    if (!this.child?.stdin?.writable || this.child.stdin.destroyed) throw new Error('pipe closed');
    this.child.stdin.write(PREFIX + JSON.stringify(frame) + '\n');
  }

  async request(endpoint, payload, signal) {
    if (endpoint === 'state') return success(this.state());
    if (this.disposed || signal?.aborted) return failure('cancelled', '操作已取消。');
    if (endpoint === 'start') return success(this.start());
    if (endpoint !== 'action') return failure('unknown-operation', '不支持的 Mimi 操作。');
    const { id, action } = payload || {};
    if (typeof id !== 'string' || !/^[\w-]{8,100}$/.test(id) || !action || typeof action !== 'object'
        || Array.isArray(action) || !OPS.has(action.op)) {
      return failure('invalid-action', '操作格式不正确。');
    }
    const fingerprint = JSON.stringify(action);
    if (fingerprint.length > 4096) return failure('invalid-action', '操作内容过长。');
    const previous = this.completed.get(id) || this.pending.get(id);
    if (previous) {
      if (previous.fingerprint !== fingerprint) return failure('duplicate-id', '请重新提交这次操作。');
      return previous.result || previous.promise;
    }
    if (!this.state().available || this.getConfig().companionEnabled === false) {
      return failure('unavailable', this.message || 'Mimi 暂未就绪，请稍后重试。');
    }
    if (this.pending.size >= 16) return failure('busy', '操作较多，请稍后再试。');
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    const timer = this.setTimer(() => {
      this.pending.delete(id);
      const result = failure('timeout', '未收到操作确认，请刷新状态后再操作。');
      this.remember(id, fingerprint, result);
      resolve(result);
    }, this.actionTimeoutMs);
    const item = { fingerprint, promise, resolve, timer };
    this.pending.set(id, item);
    try { this.send({ type: 'action', id, action }); }
    catch {
      this.pending.delete(id);
      this.clearTimer(timer);
      resolve(failure('unavailable', 'Mimi 通信已断开。'));
    }
    return promise;
  }

  rejectPending(code, message) {
    for (const [id, item] of this.pending) {
      this.clearTimer(item.timer);
      const result = failure(code, message);
      this.remember(id, item.fingerprint, result);
      item.resolve(result);
    }
    this.pending.clear();
  }

  async stop() {
    if (this.startTimer !== null) this.clearTimer(this.startTimer);
    this.startTimer = null;
    this.rejectPending('stopped', 'Mimi 正在退出。');
    if (this.stopping) return this.stopping;
    const running = this.child;
    if (!running) return;
    this.phase = 'stopping';
    this.message = 'Mimi 正在保存并退出…';
    this.stopping = new Promise(resolve => {
      let settled = false;
      let timer;
      const done = () => {
        if (settled) return;
        settled = true;
        this.clearTimer(timer);
        if (this.child === running) this.child = null;
        resolve();
      };
      running.once('exit', done);
      running.once('error', done);
      timer = this.setTimer(() => {
        try { running.kill(); } catch { /* process already gone */ }
        done();
      }, this.shutdownMs);
      try { this.send({ type: 'shutdown' }); } catch {
        try { running.kill(); } catch { /* process already gone */ }
        done();
      }
    });
    await this.stopping;
    this.stopping = null;
    this.phase = 'stopped';
    this.message = 'Mimi 已停止。';
  }

  async dispose() {
    this.disposed = true;
    await this.stop();
  }
}
