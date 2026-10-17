// Pi 扩展：科研工具薄壳 + 确认门禁。
// 工具 schema 启动时从桥拉取（单一事实来源 agent/tools.py::openai_tools），
// 每个工具把参数 POST 到桥的 /api/pi/tools/{name}，业务逻辑全部在 Python 侧。
// 环境变量：PI_BRIDGE_URL / PI_BRIDGE_TOKEN / PI_CONV（由 bridge/sessions.py 注入；
// 手动跑 pi 命令时没有这些变量，扩展自动不加载。）
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const CONFIRM_TOOLS = new Set(["stop_task", "submit_task", "save_report"]);
const DANGEROUS_BASH =
  /(rm\s+-rf|git\s+push\s+.*--force|git\s+reset\s+--hard|shutdown|reboot)/i;

interface FnDef {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

function bridgeCfg() {
  const url = process.env.PI_BRIDGE_URL;
  const token = process.env.PI_BRIDGE_TOKEN;
  const conv = process.env.PI_CONV || "default";
  if (!url || !token) return null;
  return { url, token, conv };
}

async function callBridge(name: string, args: Record<string, unknown>) {
  const cfg = bridgeCfg();
  if (!cfg) throw new Error("bridge env missing");
  const res = await fetch(`${cfg.url}/api/pi/tools/${name}`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-bridge-token": cfg.token },
    body: JSON.stringify({ args, conv: cfg.conv }),
  });
  if (!res.ok) throw new Error(`bridge ${name} HTTP ${res.status}`);
  const data = (await res.json()) as { result: string };
  return data.result;
}

function resultOf(text: string, isError: boolean) {
  return { content: [{ type: "text" as const, text }], details: {}, isError };
}

export default async function (pi: ExtensionAPI) {
  const cfg = bridgeCfg();
  if (!cfg) return; // 手动 pi CLI 会话：不注入科研工具

  // 1) 动态拉取并注册 11 个科研工具
  let defs: FnDef[] = [];
  try {
    const res = await fetch(`${cfg.url}/api/pi/tools/schema`, {
      headers: { "x-bridge-token": cfg.token },
    });
    if (res.ok) {
      const data = (await res.json()) as { tools: { function: FnDef }[] };
      defs = data.tools.map((t) => t.function);
    }
  } catch {
    // 桥暂不可达（如服务器重启中）：不阻塞会话，本轮无科研工具
  }
  for (const f of defs) {
    pi.registerTool({
      name: f.name,
      label: f.name,
      description: f.description,
      promptSnippet: `${f.name}: ${f.description.split("。")[0]}。`,
      parameters: f.parameters as never,
      async execute(_toolCallId, args) {
        try {
          const text = await callBridge(f.name, (args || {}) as Record<string, unknown>);
          let isError = false;
          try { isError = JSON.parse(text).ok === false; } catch { /* 非 JSON 结果 */ }
          return resultOf(text, isError);
        } catch (e) {
          return resultOf(`工具 ${f.name} 调用失败: ${e}`, true);
        }
      },
    });
  }

  // 2) update_plan：维护计划进度卡
  pi.registerTool({
    name: "update_plan",
    label: "Update plan",
    description:
      "更新当前任务的计划/待办清单（网页端渲染为进度卡）。开始多步任务时先建计划；每完成一步把对应 step 的 status 改为 done；正在做的改为 in_progress。",
    promptSnippet:
      "update_plan: 多步任务开始前建立计划，之后随进度更新 step 状态（pending/in_progress/done）。",
    parameters: {
      type: "object",
      properties: {
        steps: {
          type: "array",
          items: {
            type: "object",
            properties: {
              title: { type: "string" },
              status: { type: "string", enum: ["pending", "in_progress", "done"] },
            },
            required: ["title", "status"],
          },
        },
      },
      required: ["steps"],
    } as never,
    async execute(_toolCallId, args) {
      try {
        await callBridge("update_plan", (args || {}) as Record<string, unknown>);
        return resultOf("计划已更新", false);
      } catch (e) {
        return resultOf(`update_plan 失败: ${e}`, true);
      }
    },
  });

  // 3) 确认门禁：危险工具/命令先经网页弹窗
  pi.on("tool_call", async (event, ctx) => {
    const name = event.toolName;
    let what = "";
    if (CONFIRM_TOOLS.has(name)) {
      what = `工具 ${name}\n参数: ${JSON.stringify(event.input ?? {})}`;
    } else if (name === "bash") {
      const cmd = String((event.input as { command?: string })?.command ?? "");
      if (DANGEROUS_BASH.test(cmd)) what = `危险命令: ${cmd}`;
    }
    if (!what) return;
    const ok = await ctx.ui.confirm("确认操作", `${what}\n\n允许执行吗？`, {
      timeout: 180_000,
    });
    if (!ok) return { block: true, reason: "用户拒绝了该操作（或 180 秒超时未确认）" };
  });
}
