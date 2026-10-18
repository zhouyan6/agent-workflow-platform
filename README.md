# GPU Workbench · 科研智能体工作台

对话式科研智能体：GPU/任务/日志/论文/对照全部经对话完成（AgentOps 形态，Vue 3 无构建），
Run 工作流 + 危险操作人工确认门禁。旧 7 页导航前端已下线，REST API 保留（wb CLI 与工具仍用）。

## 使用

- 启动：`WORKBENCH_MOCK=1 .venv-win/Scripts/python server.py`，浏览器开 `http://127.0.0.1:8787/`
- 未登录时页面内直接登录（账号同工作台）
- 左栏：新建/切换/删除会话、机器面板（本期仅本地，多服务器执行器见后续期）
- 对话：流式回复 + 思考过程折叠 + 工具调用卡 + ⚠ 确认卡（提交训练/停任务/保存报告/危险 bash 需人工确认，180s 超时视为拒绝）
- Run 工作流：让智能体「建一个 run」→ 顶部进度条，阶段状态随 `run_update` 更新
- PDF：拖进输入框自动上传抽取，智能体接力给复现要点（repro_check）
- 设置齿轮：LLM 配置（base_url/model/api_key）
- 任务结束/失败会自动注入系统事件并唤醒智能体分析汇报

## 本地开发

- 依赖：`.venv-win`（Python 3.13）+ Node ≥ 20 + 全局 `@earendil-works/pi-coding-agent`
- 测试：`.venv-win/Scripts/python -m pytest tests/ -q`（49 个单测）
- LLM 密钥：读 `config.json` 的 `api_key`，经 `ZHIPU_API_KEY` 环境变量注入 Pi
- Pi provider 配置：`~/.pi/agent/models.json`（zhipu，openai-completions，thinkingFormat zai）
- Pi 扩展：改 `pi-extension/research/index.ts` 后需重新拷贝到
  `C:\Users\ZhouYan\.pi\agent\extensions\research\index.ts` 并**新建会话**生效
  （Pi 会把工具清单持久化进会话，旧会话恢复时仍用旧清单）

### 桥架构（bridge/）

- `pi_process.py`：spawn `pi --mode rpc`，JSONL 读写；Windows 下 stop 用 taskkill 整树杀
- `pi_client.py`：RPC 命令封装（id 关联 response）
- `events.py`：Pi 事件 → 网页 WS 消息（token/thinking/tool_*/confirm_request/plan_update/run_update/agent_event/history）
- `sessions.py`：会话管理（pi-sessions/index.json）、bridge token、run/plan 推送、任务事件注入
- `tool_backend.py`：localhost-only 工具后端；`agent.tools.dispatch` 复用 11 个业务工具 + BRIDGE_TOOLS（run_create/run_update/ccf_lookup/update_plan）
- `runs.py`：Run 工作流持久化（runs.json）
- `watcher.py`：只读轮询 workbench.db，任务结束注入 agent_event
- `routes.py`：`/api/pi/ws`、`/api/pi/convs`、`/api/pi/runs`、启动 watcher

### E2E 验收脚本（手动）

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8787/api/login -H "Content-Type: application/json" \
  -d '{"username":"...","password":"..."}' | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
.venv-win/Scripts/python tests/e2e_tools.py "$TOKEN"   # 工具链 + 确认门禁
.venv-win/Scripts/python tests/e2e_ui.py "$TOKEN"      # 历史回放 + run_update（用新会话名跑）
```

## 部署（实验室服务器）

见 `deploy.sh`（rsync + tmux，crontab @reboot 自启）。
