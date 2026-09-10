# Changelog

## 0.6.4 — 2026-09-10

- 内置 Computer Use 升级到 `@milkuovo/dsh-computer-use@0.2.1`（此前内置 0.2.0）。
- Computer Use 去重守卫改为在 `Config` 校验通过后才占用：非法配置不再把进程级守卫永久留在已占用状态，后续重试不会被"已激活"挡掉。
- Computer Use 标记吸附增加最小边长上限：只有尺寸合理的控件才能承载吸附，避免把点击吸附到视频播放器、页面背景这类大容器的中心而偏离标记。
- Computer Use 的 peerDependencies 对齐 DSH 0.1.2+（cordis `^4.0.2`、schemastery `^3.18.2`、dsh-tools `^0.1.2-rc.1`）。
- Mimi 自身代码与动作素材与 0.6.3 相同（DSH 0.1.5 `assistant-stream` 适配已在 0.6.3 完成）。

## 0.6.3 — 2026-09-10

- 适配 DeepSeek Harness `0.1.5-rc.1`：`session/follow` 请求显式开启 `assistantStream`。DSH 0.1.5 起实时 assistant 增量改为 opt-in，不开启时桌宠只能看到工具事件而收不到流式文本与思考。
- 新增 `assistant-stream` 帧解码：处理 `start` / `chunk` / `end`，`chunk` 从对应 `start` 继承 turn/step，按 attemptId 与 index 去重，并保留旧 chunkrow 紧凑记录兼容路径。
- 持久化 `assistant/attempt` 内的紧凑流（`text-chunks`、`reasoning-chunks`、`tool-call-chunks`、`chunk`）展开回 `assistant/chunk` 的 `text-delta`、`reasoning-delta`、`tool-call-delta`，follow 重连的 baseline 回放不再丢内容。
- 已在 DSH `0.1.5-rc.1` + `dsh web` 实机验证：插件树加载、桌宠子进程 spawn、Computer Use 工具注册、认证 RPC 与 Remote mux 事件链路全部正常。
- 内置 Computer Use `0.2.0` 与动作素材不变；258 项 Python 测试通过（bridge 与 remote mux 回归 52 项）。

## 0.6.2 — 2026-09-05

- 代码审查修复：双击可唤醒睡眠中的桌宠（原守卫顺序令该分支不可达，与文档承诺不符），并补回归测试。
- DSH 消息气泡层记录锚点并在首次显示前定位，新气泡不再在屏幕左上角闪现一帧。
- 移除 tick 中引用已删除 `FORTUNE_TEXTS` 的 fortune 气泡死代码块（对应动作已在 0.6.0 资产精简中退役）。
- 小修：`affection` 去重 `source_label` 定义；`DshBridge` 新增公开 `auth` 访问器，集成层不再跨对象访问私有属性；`plugin_update` 补文件末尾换行。
- 内置 Computer Use `0.2.0` 与动作素材不变；255 项 Python 测试通过。

## 0.6.1 — 2026-09-04

- 适配 DeepSeek Harness `0.1.2-rc.1`：使用 `authenticatedUrl` 完成 Python 端一次性认证交换，认证 Cookie 仅保存在内存中。
- 统一新版 Remote RPC：使用 slash endpoint 与 `payload.args` envelope，并修正 session/model 相关 wire 参数。
- 接入 `remote.mux` 的 `$events`、`session/control`、`session/follow` 流；等待 `ready` 后报告连接，并支持 follow 重连、baseline 回放和序列去重。
- 修正工作模式 session 目标选择与 follow 同步，避免固定项目、自动选择和异步 poll 之间串线。
- 新增桌宠模型目录/选择与 reasoning effort 菜单，和本地回复摘要模型保持隔离。
- 保持内置 Computer Use `0.2.0`；已在 DSH `0.1.2-rc.1` + Mimi 实机验证观察、点击和操作后观察闭环。
- 258 项 Python 测试通过；Remote mux、窗口 E2E、GUI smoke 和 npm 包运行时检查通过。

## 0.6.0 — 2026-09-03

- 内置 `@milkuovo/dsh-computer-use@0.2.0`，Computer Use 直接随 Mimi 安装，提供截图、UI Automation 树、点击、输入、滚动、拖动、按键与操作后验证。
- 强制观察→动作→验证闭环，支持多窗口绑定、编号网格、落点自证、审计和可选逐动作审批。
- 工作模式与桌宠模式进一步区分：工作模式按 DSH 活动显示输入栏，桌宠模式悬停头部后显示聊天输入，并显示模式徽标。
- 新增持久化好感度：桌宠模式会分析正向聊天内容，默认每次 `+1`，90 秒冷却、每日最多 3 点、同文指纹去重；工作模式、工具调用、任务结果和问题卡回答不改变好感度。
- Agent 内置人格加入安全转译的 DeepSeek 鲸鱼娘社区设定，并按关系阶段调整语气；保留破坏性操作确认、隐私边界和外部影响确认规则。
- Windows 中文输入法继续使用稳定的 `QLineEdit` 路径；气泡、活动胶囊、DSH session 隔离和跨线程事件回投保持不变。
- 218 项应用测试通过，重新构建 `mimi-desktop-pet-0.6.0.tgz` 发布包。

## 0.5.0 — 2026-08-30

- 表情系统根治：v5 Live 从"整图替换"改为"分区补丁叠加"，眨眼不再吃掉微笑、虹膜追踪在说话/微笑下不跳变，任何表情状态可自由组合。
- 思维链内容不再被总结；摘要功能改为压缩 assistant 的正常输出（长回复弹"总结：…"气泡），短回复不打扰。
- 新增 npm 插件更新提醒：连接 DSH 后自动比对 registry，发现新版用气泡和右键菜单提示（适配 DSH profiles 安装点）。
- 修正 DSH 插件版本解析：deepseek-official 内置端点、`refs` 嵌套凭据、%APPDATA% 动态寻路。
- 179 项应用测试通过；重建 0.5.0 发布包（素材与代码均为最新）。

## 0.4.0 — 2026-08-24

- 适配并实机验证 DeepSeek Harness `0.1.1-rc.2`。
- 运行库从 57 套历史动作精简为 23 套正式核心动作，PNG 运行副本替换为透明 WebP Q95。
- 加入 Live Rig v5：脚底锚定呼吸、眨眼、微笑、说话和独立虹膜追踪。
- 重做 DSH 交互：头顶气泡、Windows 中文输入、项目联动与独立 Mimi 管家 Agent。
- 加入五分区触摸、组合互动、拖放投喂、久坐久睡场景链与单实例保护。
- 发布包约 47.9 MiB；不包含旧 Rig、候选源图、本机会话、测试或缓存。
- 162 项应用测试通过，并完成 rc.2 HTTP RPC、WebSocket 与插件生命周期烟测。
- 新增 34 秒 1080p 宣传视频与海报。

## 0.3.0 — 2026-08-19

- 加入 DSH 活动胶囊、任务摘要、多项目切换和完整桌宠本体打包。
