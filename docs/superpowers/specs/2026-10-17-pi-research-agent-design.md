# 科研执行智能体设计（Pi 内核 + FastAPI 桥）

日期：2026-10-17
状态：待审阅

## 1. 目标与非目标

### 目标

把现有 GPU 工作台的「助手」升级为科研执行智能体，能够自主规划并执行闭环：

**复现论文 → 提交训练 → 监控日志 → 分析结果 → 修改代码 → push GitHub → 重跑**

人在关键操作点确认（提交训练、停任务、保存报告等）。

约束与决策：

- 大脑跑在本机（Windows 11），GPU 实验跑在远端服务器
- LLM 继续用智谱 GLM（OpenAI 兼容 API）
- Agent runtime 选用 Pi（earendil-works/pi-coding-agent，Node）
- 交互界面沿用现有网页助手页
- 实验代码在本机修改，经 GitHub 同步到训练服务器
- 训练服务器可选、形态混合：有的装了工作台（REST），有的是裸机（SSH）

### 非目标

- 不做多用户改造（本地单用户使用，账号体系保留仅因现有代码依赖）
- 不引入向量数据库（跨会话记忆用现有 SQLite 案例库）
- 不做新的前端框架迁移（继续原生 JS）
- 不替换论文抽取、悬浮球聊天等现有 GLM 单次调用功能

## 2. 总体架构

```
浏览器（现有助手页，升级：计划卡/工具卡/确认弹窗）
   │ WebSocket（沿用 token/tool/confirm 事件风格）
   ▼
FastAPI 桥（Python，本地 :8787，复用 server.py 进程）
   ├─ PiSessionManager：网页会话 ↔ Pi session 文件
   │    每会话 spawn 一个 `pi --mode rpc --session-dir sessions/` 子进程
   ├─ RPC 客户端：JSONL 协议（stdin 命令 / stdout 事件），转发到 WebSocket
   ├─ 内部工具后端：localhost-only REST 端点，import 复用现有 Python 模块
   └─ 执行器层：servers.json 注册表
        ├─ WorkbenchExecutor（REST：提交任务/日志/指标/GPU）
        └─ SSHExecutor（裸机：git pull + nohup 训练 + tail 日志 + nvidia-smi）

Pi 子进程（Node）
   ├─ GLM provider（models.json：zhipu，openai-completions，thinkingFormat "zai"）
   ├─ 内置工具：read / edit / write / grep / find / bash / powershell
   ├─ 项目扩展 .pi/extensions/：科研工具薄壳 + 确认门禁钩子
   └─ 技能 .pi/skills/：由现有 agent/skills/SKILL.md 迁移
```

### 薄壳原则

TypeScript 扩展只做协议适配（schema、调桥、截断输出），业务逻辑全部留在 Python 桥。`experiments.py`（JSONL 指标解析）、`db.py`（论文/案例库）等现有逻辑零移植。

## 3. 组件设计

### 3.1 FastAPI 桥（新增 `bridge/` 包）

| 模块 | 职责 |
|---|---|
| `bridge/pi_process.py` | Pi 子进程生命周期：spawn（`pi --mode rpc --session-dir <ROOT>/sessions`）、按 LF 切分 JSONL、请求 id 关联、`agent_settled` 等待、崩溃重启（最多 3 次） |
| `bridge/pi_client.py` | RPC 命令封装：`prompt` / `get_state` / `get_entries` / `abort` / `set_model` 等 |
| `bridge/events.py` | Pi 事件 → 网页 WebSocket 消息的翻译（见 3.4） |
| `bridge/tool_backend.py` | localhost-only 端点：gpu_status / submit_train / log_tail / best_result / exp_compare / paper_lookup / save_report / stop_task / update_plan |
| `bridge/executors.py` | WorkbenchExecutor + SSHExecutor，统一接口 |
| `bridge/servers.py` | servers.json 读写、健康检查 |

会话模型：网页里一个「对话」对应一个 Pi 子进程。切换对话 = 结束旧子进程 + 用对应 session 文件起新子进程。桥重启后通过 `--resume` 恢复。

### 3.2 Pi 扩展（`.pi/extensions/research/`）

自定义工具（全部薄壳，指向桥的内部端点）：

| 工具 | 参数（要点） | 说明 |
|---|---|---|
| `list_servers` | — | 注册表 + 各服务器 GPU 概览 |
| `gpu_status` | server? | 指定或全部服务器的卡状态 |
| `submit_train` | server, command, gpu?, expected_hours? | 危险工具，需确认；先经桥触发远端 git pull |
| `log_tail` | server, task, lines? | 运行中任务日志尾部（含 NaN/OOM 标记，复用现有诊断逻辑） |
| `best_result` | server, task/experiment | 解析日志取最优指标 |
| `exp_compare` | experiment, paper? | 与论文对照指标比较 |
| `paper_lookup` | query | 本地论文库检索 |
| `save_report` | title, markdown | 危险工具，需确认；写入 artifacts |
| `stop_task` | server, task | 危险工具，需确认 |
| `update_plan` | steps[] | 维护计划状态，桥渲染进度卡 |

内置工具（read/edit/write/grep/find/bash）不做包装，直接可用——本地改代码、git commit/push、跑小验证脚本都靠它们。

### 3.3 确认门禁

扩展注册 `tool_call` 钩子：

- 危险工具名单：`submit_train` / `stop_task` / `save_report`，以及 `bash` 命令命中危险模式（`rm -rf`、`git push --force`、`git reset --hard` 等，可配置）
- 命中时调 `ctx.ui.confirm(title, message, { timeout: 180_000 })`
- RPC 模式下 Pi 发 `extension_ui_request`（stdout）→ 桥转发网页弹窗 → 用户点击 → 桥回 `extension_ui_response`（stdin）→ 扩展返回结果
- 超时/拒绝 → `return { block: true, reason }`，智能体收到拒绝原因可调整方案

### 3.4 事件翻译（Pi RPC → 网页 WebSocket）

| Pi 事件 | 网页消息 |
|---|---|
| `message_update` (text_delta / thinking_delta) | token 流（思维链单独通道） |
| `tool_execution_start/_update/_end` | 工具卡（名称、参数、进度、结果摘要） |
| `extension_ui_request` (confirm) | 确认弹窗 |
| `bash_execution_update` | 工具卡（bash 类） |
| `agent_settled` | 回合结束 |
| `agent_start` / `turn_*` | 状态指示 |

网页 → 桥 → Pi：用户消息 = `prompt`（流式期间用 `steer`/`followUp`）；中断 = `abort`。

### 3.5 服务器注册表与执行器

`servers.json`（ROOT 下，示例）：

```json
[
  {"name": "inspur", "type": "workbench",
   "base_url": "http://127.0.0.1:8787", "username": "niexin", "password_env": "WB_INSPUR_PW"},
  {"name": "gpu2", "type": "ssh",
   "host": "gpu2.lab.edu", "user": "zhouyan", "port": 22,
   "repo_path": "~/exp-repo", "run_dir": "runs/"}
]
```

统一执行器接口：

```
gpu_status() -> list[GPU]
list_tasks(active_only) -> list[Task]
submit(command, gpu, repo_sync) -> Task
logs(task, lines) -> str
stop(task) -> ok
```

- WorkbenchExecutor：登录（密码从环境变量读）拿 session cookie，调现有 `/api/gpu` `/api/tasks` 等
- SSHExecutor：Windows OpenSSH 免密（ssh-keygen + authorized_keys，一次性配置）；提交 = `git pull && nohup <command> > run.log 2>&1 &`，进程组记录 pid 到桥侧登记表（裸机任务状态由桥跟踪：pid 存活检查 + 日志 mtime）

### 3.6 记忆

- 会话内：Pi sessions 持久化 + 自动压缩（内置）
- 跨会话：现有 SQLite `agent_cases`（失败案例）与用户 profile；会话启动时由桥拼成一条上下文消息注入（不进系统提示词，随会话可压缩）
- 领域知识：现有 `agent/skills/*/SKILL.md` 迁到 `.pi/skills/`

### 3.7 LLM 配置

`~/.pi/agent/models.json`：

```json
{"providers": {"zhipu": {
  "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
  "api": "openai-completions",
  "apiKey": "${ZHIPU_API_KEY}",
  "compat": {"thinkingFormat": "zai"},
  "models": [{"id": "glm-4.7", "contextWindow": 200000}]
}}}
```

密钥从 `ZHIPU_API_KEY` 环境变量读（Pi 支持 `$ENV` 插值），与现有 config.json 的密钥保持一致来源。

## 4. 旧代码处置

| 现有 | 处置 |
|---|---|
| server.py 各页面 API（论文/日志/对照/设置/认证） | 保留原样 |
| `agent/routes.py` `/api/agent/*` | 替换为桥的新端点（前端助手页改指向新端点） |
| `agent/loop.py`、`agent/tools.py` 的 schema/循环 | 退役（业务函数被 tool_backend 复用） |
| `agent/store.py` 会话表 | 退役（Pi sessions 接管）；`agent_cases`/profile 表保留 |
| `agent/jobs.py` 每晚复盘 | 保留，改为调桥的批量摘要端点 |
| chat.py 悬浮球/论文抽取 | 保留不动 |
| deploy.sh / tmux 部署 | 部署回服务器时照旧（服务器侧也可装 Node 跑同架构） |

本地运行环境：`.venv-win`（已创建）+ Node ≥ 20 + `npm install -g @earendil-works/pi-coding-agent`；无卡开发用 `WORKBENCH_MOCK=1`。

## 5. 错误处理

- Pi 子进程崩溃：桥捕获非零退出，重启（保留 session resume），网页提示「引擎已重启，对话已恢复」；连续 3 次失败则停并报错
- RPC 违反协议（如 stdout 出现非 JSON 行）：按行丢弃并记日志，不中断
- 执行器失败（SSH 不通/工作台 5xx）：工具返回 isError，智能体自行决定换服务器或报告用户
- 确认超时：视为拒绝，原因写明「用户未确认（180s 超时）」
- GLM 流式中断：依赖 Pi 内置 auto-retry（`set_auto_retry` 开启）

## 6. 测试策略

- `bridge/` 单元测试：JSONL 切分（含 U+2028 边界）、事件翻译、执行器（SSH 用本地假命令、workbench 用 httpx MockTransport）
- 扩展薄壳：`pi -p` 冒烟（print 模式，无 UI），验证 schema 与桥端点契约
- 端到端：本地起桥 + Pi，`WORKBENCH_MOCK=1`，走通「提问 → 工具调用 → 确认 → 结果」；真机验收在 inspur 上做一次提交训练
- 保留 `agent/selftest.py` 思路，改写为桥的自测脚本

## 7. 实施分期

1. **跑通骨架**：Node/Pi 安装、models.json、桥 spawn Pi、网页能对话（无工具）
2. **工具与门禁**：扩展薄壳 + 桥工具后端（本地可测部分）+ 确认弹窗
3. **双执行器**：servers.json、Workbench/SSH 执行器、submit_train 真实链路（含 git pull）
4. **记忆与计划卡**：案例库注入、update_plan 渲染、skills 迁移、每晚复盘接桥
5. **清理**：下线旧 `/api/agent/*`，前端助手页定型，文档

每期结束可用 demo 验收；1–3 期完成后即形成最小可用闭环。

## 8. 风险

| 风险 | 缓解 |
|---|---|
| Pi 版本迭代快（0.99.x） | 锁版本安装；RPC 协议以官方文档为准，桥做兼容层 |
| GLM 长工具循环稳定性 | 确认门禁拦截危险操作；auto-retry；每回合轮数靠 Pi 内置管理 |
| Windows SSH 免密配置摩擦 | 一次性手动配置，文档化；失败时工具报错指路 |
| 裸机任务状态跟踪弱于工作台队列 | pid 登记表 + 日志 mtime 兜底；建议主力训练尽量用装工作台的服务器 |
