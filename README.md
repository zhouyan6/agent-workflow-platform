# GPU Workbench

论文复现工作台：GPU 监控、任务队列、论文调研、实验对照、智能体助手。

## 本地开发（第 1 期：Pi 骨架）

- 依赖：`.venv-win`（Python 3.13）+ Node ≥ 20 + 全局 `@earendil-works/pi-coding-agent`
- 启动：`WORKBENCH_MOCK=1 .venv-win/Scripts/python server.py`
- 页面：登录主站后开 `/static/pi-chat.html`
- 测试：`.venv-win/Scripts/python -m pytest tests/ -v`
- LLM 密钥：读 `config.json` 的 `api_key`，经 `ZHIPU_API_KEY` 环境变量注入 Pi
- Pi provider 配置：`~/.pi/agent/models.json`（zhipu，openai-completions，thinkingFormat zai）

### 桥架构（bridge/）

- `pi_process.py`：spawn `pi --mode rpc`，JSONL 读写（只按 `\n` 分行）
- `pi_client.py`：RPC 命令封装（id 关联 response）
- `events.py`：Pi 事件 → 网页 WS 消息（token/thinking/tool_*/confirm_request/turn_*）
- `sessions.py`：会话管理，`pi-sessions/index.json` 记录 conv → 会话文件（Pi 文件名不含会话名）
- `routes.py`：`/api/pi/ws` WebSocket 端点（cookie 鉴权复用 server.resolve_token）

### E2E 验收脚本（手动）

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8787/api/login -H "Content-Type: application/json" \
  -d '{"username":"...","password":"..."}' | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
.venv-win/Scripts/python tests/e2e_manual.py "$TOKEN"        # 单轮对话
.venv-win/Scripts/python tests/e2e_resume.py "$TOKEN" first   # 记暗号
# 重启 server.py 后
.venv-win/Scripts/python tests/e2e_resume.py "$TOKEN" second  # 验证恢复
```

## 部署（实验室服务器）

见 `deploy.sh`（rsync + tmux，crontab @reboot 自启）。
