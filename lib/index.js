/** Mimi's DSH host: settings, embedded-panel RPC and the owned pet process. */
import { appendFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import z from '@deepseek-ai/schemastery';
import { apply as applyComputerUse } from '@milkuovo/dsh-computer-use';
import { CHANNEL, PetRuntime } from './pet-runtime.js';

export const name = 'mimi-pet';
export const inject = ['connection', 'webServer'];

export const Config = z.object({
  enabled: z.boolean().default(true).description('启用 Mimi 桌宠（随 DSH 启动和退出）'),
  companionEnabled: z.boolean().default(true).description('在 DSH 内启用 Mimi 专注、勿扰与提醒（更改后随插件重启生效）'),
  focusMinutes: z.number().min(1).max(240).step(1).default(25).description('默认专注时长，单位分钟（更改后随插件重启生效）'),
  petDir: z.string().default('').description('可选本地项目目录；留空优先使用插件内的桌宠运行文件'),
  python: z.string().default('').description('pythonw.exe 完整路径（留空自动探测）'),
  scale: z.number().default(100).description('桌宠缩放百分比（1–200）'),
  computerUseEnabled: z.boolean().default(true).description('启用 Windows 界面观察、点击、输入、滚动和拖动能力'),
  computerUseAskBeforeActions: z.boolean().default(false).description('每个界面动作前都请求 DSH 批准（默认关闭；高风险操作仍由 Mimi 先向用户确认）'),
  computerUseScreenshot: z.boolean().default(true).description('观察界面时附带窗口截图（视觉模型效果更好）'),
  computerUseGrid: z.boolean().default(true).description('在观察截图上显示可点击编号标记'),
});

function resolveDshHost(webServer) {
  const configured = String(process.env.MIMI_DSH_HOST || '').trim();
  if (/^[^/:]+:\d+$/.test(configured)) return configured;
  const port = Number(process.env.MIMI_DSH_PORT || webServer?.port || 3080);
  return Number.isInteger(port) && port > 0 && port <= 65535 ? `127.0.0.1:${port}` : '127.0.0.1:3080';
}

function log(message) {
  try {
    appendFileSync(join(process.env.TEMP || '.', 'mimi-pet.log'), `[${new Date().toISOString()}] ${message}\n`);
  } catch { /* diagnostics must not interrupt DSH */ }
}

export function apply(ctx, config) {
  const connection = ctx.connection;
  const dshHost = resolveDshHost(ctx.webServer);
  const defaults = Config({});
  // DSH 0.1.7+ removed ctx.settings.register: the settings page is generated
  // from the Config export above, and every settings change restarts this
  // plugin with the freshly merged config. The host and the Python child
  // still share one configuration snapshot per plugin lifetime.
  const activeConfig = { ...defaults, ...(config ?? {}) };
  const getConfig = () => activeConfig;
  const runtime = new PetRuntime({
    getConfig,
    packageDir: join(dirname(fileURLToPath(import.meta.url)), '..'),
    getAuthUrl: () => connection?.authenticatedUrl(`http://${dshHost}/`) || '',
    log,
  });

  // Exact routes share DSH's /api carrier, authentication and Host/Origin
  // checks. They do not replace the Remote gateway's shared interceptor.
  for (const endpoint of ['state', 'action', 'start']) {
    const method = `mimi-pet/${endpoint}`;
    ctx.effect(() => connection.fetch.register({
      path: `${CHANNEL}/${method}`, methods: ['POST'], requestBody: 'buffered',
      async fetch(request) {
        let body;
        try {
          const text = await request.text();
          if (text.length > 8192) throw new Error('oversize');
          body = JSON.parse(text);
          if (body.type !== 'client-request' || body.method !== method
              || typeof body.rpcId !== 'string' || body.rpcId.length > 100) throw new Error('envelope');
        } catch {
          return new Response('Invalid Mimi request', { status: 400 });
        }
        const result = await runtime.request(endpoint, body.payload, request.signal);
        return new Response(JSON.stringify({ type: 'server-response', rpcId: body.rpcId, result }), {
          headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
        });
      },
    }), `mimi-pet: ${endpoint} endpoint`);
  }

  // Computer Use retains its own registrations and duplicate guard.
  ctx.inject(['tools'], toolsCtx => {
    const cfg = getConfig();
    if (!cfg.computerUseEnabled) return;
    applyComputerUse(toolsCtx, {
      askBeforeActions: cfg.computerUseAskBeforeActions,
      maxDepth: 8,
      maxNodes: 400,
      includeScreenshot: cfg.computerUseScreenshot,
      annotate: { grid: cfg.computerUseGrid, lastPoint: true, activate: true },
      audit: true,
      updateCheck: false,
      fx: { overlay: true, screenshot: false },
    });
  });

  ctx.effect(() => {
    runtime.scheduleStart();
    return () => runtime.dispose();
  }, 'mimi-pet: desktop pet lifecycle');
}
