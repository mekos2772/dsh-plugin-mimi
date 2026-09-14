# Mimi — DeepSeek Harness 桌宠插件

Mimi 随 DSH 启动，跟随工作会话显示思考、工具调用、提问与回复，也支持桌宠聊天。专注、勿扰和一次性提醒统一放在 **DSH 会话顶部 Mimi → 右侧面板**，不创建独立管理窗口。

- 本地开发版本：`0.7.0`，尚未发布。
- 当前交接状态：源码与后端检查已完成，DSH 中的 Mimi 入口和面板交互仍待界面验收。
- 本轮验证使用本机已有 DSH `0.1.5-rc.1`、Node.js 24、Python 3.11 和 PySide6 6.11.1。
- 已有 DSH 的用户直接沿用当前安装，无需为这组功能另装一套 DSH。
- npm：<https://www.npmjs.com/package/mimi-desktop-pet>
- 源码：<https://github.com/mekos2772/dsh-plugin-mimi>

## DSH 内嵌专注与提醒

打开一个 DSH 会话，点击顶部 **Mimi**，或在右侧面板的新标签引导页选择 Mimi。

| 操作 | 行为 |
|---|---|
| 开始专注 | 默认 25 分钟，可设置 1–240 分钟；支持暂停、继续、取消和 5 分钟休息 |
| 勿扰 | 减少自主走动和普通工具提示；保留正常回复、待回答及待批准入口 |
| 一句话安排 | 例如“陪我专注 25 分钟”“20 分钟后提醒我喝水”“明天 18:00 提醒我下班” |
| 提醒 | 添加、修改、完成、取消、稍后 10 分钟；到期后仍保留在待处理列表 |
| 最近通知 | 查看专注和提醒记录；气泡消散后仍可找回 |
| 桌宠右键或陪伴气泡 | 请求 DSH 打开 Mimi 面板；不弹出另一个窗口 |

这些操作在本机计算，不消耗模型调用。连接 DSH 后，桌宠模式输入框也识别明确的陪伴指令；工作模式中的文字仍发送到当前工作会话。

专注开始默认开启本次勿扰；暂停、取消或结束后恢复原来的独立勿扰设置。Windows 锁屏、休眠以及重启恢复后保持暂停，由用户手动继续。DSH 退出或插件卸载会保存并关闭桌宠；关闭期间不提供后台提醒，下次启动补记未送达的到期事项。

## 原有桌宠能力

- 23 套正式动作、Live Rig v5、视线跟随、呼吸、眨眼和微笑。
- 分区触摸、摸头后击掌、投喂、拖拽/落地、久坐久睡和欢迎回来。
- DSH 工作会话跟随和独立桌宠会话；头顶气泡、中文输入框及模型选择。
- 内置 Computer Use `0.2.1`：窗口观察、点击、输入、按键、滚动与拖动，沿用操作后重新观察的规则。
- 好感度只来自既有桌宠互动和受限的正向聊天；工作任务、专注、休息和提醒均不加减好感度。

## 设置与已有配置

DSH 设置中的命名空间为 **`mimi-pet`**。设置更改后重启插件或 DSH，同一插件生命周期内宿主和桌宠使用同一组配置。

| 字段 | 默认值与说明 |
|---|---|
| `enabled` | `true`，随 DSH 启动和退出 Mimi |
| `companionEnabled` | `true`，启用内嵌专注与提醒 |
| `focusMinutes` | `25`，默认专注时长，范围 1–240 分钟 |
| `petDir` | 空，优先使用插件内 `pet/`；可显式指向现有本地项目 |
| `python` | 空，自动探测 `pythonw.exe`；可指定已安装解释器的完整路径 |
| `scale` | `100`，桌宠缩放百分比，范围 1–200 |
| `computerUseEnabled` | `true`，启用 Windows 界面操作 |
| `computerUseAskBeforeActions` | `false`，是否每个界面动作都请求 DSH 批准 |
| `computerUseScreenshot` | `true`，观察时附带窗口截图 |
| `computerUseGrid` | `true`，截图显示编号点选标记 |

旧版使用的 `mimiPet` 不符合 DSH 0.1.5 的命名规则。本版不删除、覆盖或自动迁移旧设置；原 bundle 的 entry config 仍作为基础配置。如果曾在 `mimiPet` 中自定义关闭能力、Python 路径、缩放等，应在启用更新前把对应字段复制到 `mimi-pet`，保留原配置作为备份。新的 `mimi-pet` 用户设置优先于 entry config。

陪伴数据位于 `%APPDATA%\MimiDesktopPet\companion.json`，独立于好感度和 DSH 会话数据，支持原子保存及损坏备份。

## 本地开发与验证

在仓库根目录复用现有 Python、Node 和 DSH 依赖运行检查；这些命令不安装软件或修改已使用的 DSH profile：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -X utf8 -m pytest mimi_app/tests -q
node --test dsh-plugin-mimi/test/runtime.test.mjs dsh-plugin-mimi/test/client.test.mjs
node --experimental-loader ./scripts/dsh_sdk_loader.mjs scripts/verify_dsh_integration.mjs dsh-plugin-mimi/lib/index.js vendor/dsh-computer-use/lib/index.js
python -X utf8 tools/smoke_companion.py
```

需要本地交付包时，在仓库根目录运行 `python scripts/build_dsh_package.py --out reports/companion-20260914/package`。打包只生成本地 `.tgz`，不安装、不发布。包内含宿主、DSH 客户端、Python 运行时及正式素材；不含用户状态或缓存。

详细使用和验证边界在仓库的 `docs/MIMI_COMPANION_FEATURES.md` 与 `reports/companion-20260914/VALIDATION.md`。本轮真实接口验证不等同于全部模型、多项目和系统休眠场景均已验证。

## 许可

MIT
