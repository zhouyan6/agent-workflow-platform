/* AgentOps 形态科研智能体工作台（Vue 3 无构建） */
const { createApp } = Vue;

const App = {
  data() {
    return {
      me: null, convs: [], conv: "", msgs: [], input: "", ws: null,
      status: "未连接", streaming: false, curAi: null, curThink: null,
      run: null, gate: null, drag: false,
      settingsOpen: false, settings: {}, chatCfg: {}, busy: false,
    };
  },
  template: `
  <div>
    <aside class="sidebar">
      <div class="brand">科研智能体</div>
      <button class="new" @click="newConv">+ 新建会话</button>
      <div class="sec">会话</div>
      <div v-for="c in convs" :key="c.id" class="conv"
           :class="{active: c.id === conv}" @click="openConv(c.id)">
        {{ c.title }} <span class="del" @click.stop="delConv(c.id)">×</span>
      </div>
      <div class="sec">机器</div>
      <div class="machine">● 本地（mock）</div>
      <div class="foot"><span>{{ me ? me.username : "" }}</span>
        <span @click="logout" style="cursor:pointer">登出</span></div>
    </aside>
    <main class="main">
      <div class="topbar"><b>{{ currentTitle }}</b>
        <span style="cursor:pointer" @click="openSettings">⚙ 设置</span></div>
      <div class="runbar" v-if="run">
        <b style="font-size:12px">{{ run.goal }}</b>
        <span v-for="s in run.stages" :key="s.title" class="stage"
              :class="s.status">{{ s.title }}</span>
      </div>
      <div class="stream" ref="stream">
        <div v-for="(m, i) in msgs" :key="i" class="msg" :class="m.role">
          <div class="bubble">
            <div v-if="m.think" class="think" @click="m.showThink = !m.showThink">
              💭 思考过程（点击展开/折叠）
              <div v-if="m.showThink">{{ m.think }}</div>
            </div>
            <div class="markdown" v-text="m.text"></div>
          </div>
        </div>
        <div v-if="gate" class="gate">
          <b>⚠ 需要确认</b>
          <div style="white-space:pre-wrap;font-size:13px;margin:6px 0">{{ gate.text }}</div>
          <button @click="answerGate(true)">确认执行</button>
          <button @click="answerGate(false)">拒绝</button>
        </div>
      </div>
      <div class="composer" :class="{drag}" @dragover.prevent="drag = true"
           @dragleave="drag = false" @drop.prevent="dropPdf">
        <textarea v-model="input" placeholder="输入消息（Enter 发送，Shift+Enter 换行；PDF 拖到这里上传）"
                  @keydown="keydown"></textarea>
        <div class="row"><span>{{ status }}</span>
          <button class="send" @click="send">↑</button></div>
      </div>
      <div v-if="settingsOpen" class="modal-mask" @click.self="settingsOpen = false">
        <div class="modal">
          <h3>设置</h3>
          <template v-for="f in ['base_url','model','api_key']" :key="f">
            <label>LLM {{ f }}</label>
            <input v-model="chatCfg[f]">
          </template>
          <button @click="saveSettings">保存</button>
        </div>
      </div>
    </main>
  </div>`,
  computed: {
    currentTitle() {
      const c = this.convs.find(x => x.id === this.conv);
      return c ? c.title : "新会话";
    },
  },
  async mounted() {
    const r = await fetch("/api/me", { credentials: "include" });
    if (!r.ok) { location.href = "/login"; return; }
    this.me = await r.json();
    await this.loadConvs();
    if (this.convs.length) this.openConv(this.convs[0].id);
    else this.newConv();
  },
  methods: {
    async loadConvs() {
      const r = await fetch("/api/pi/convs", { credentials: "include" });
      if (r.ok) this.convs = (await r.json()).convs;
    },
    newConv() {
      const id = "c" + Date.now().toString(36);
      this.convs.unshift({ id, title: "新会话", updated: new Date().toISOString() });
      this.openConv(id);
    },
    async delConv(id) {
      if (!confirm("删除该会话及其历史？")) return;
      await fetch("/api/pi/convs/" + id, { method: "DELETE", credentials: "include" });
      await this.loadConvs();
      if (this.conv === id) { this.convs.length ? this.openConv(this.convs[0].id) : this.newConv(); }
    },
    openConv(id) {
      this.conv = id; this.msgs = []; this.run = null; this.gate = null;
      this.loadRuns();
      this.connect();
    },
    async loadRuns() {
      const r = await fetch("/api/pi/runs?conv=" + encodeURIComponent(this.conv),
                            { credentials: "include" });
      if (r.ok) { const runs = (await r.json()).runs; if (runs.length) this.run = runs[runs.length - 1]; }
    },
    connect() {
      if (this.ws) { try { this.ws.close(); } catch (e) {} }
      this.status = "连接中…";
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/api/pi/ws?conv=${encodeURIComponent(this.conv)}`);
      this.ws = ws;
      ws.onopen = async () => { this.status = "已连接"; };
      ws.onclose = () => { this.status = "已断开"; };
      ws.onmessage = e => this.handle(JSON.parse(e.data));
    },
    handle(m) {
      if (m.type === "history") {
        this.msgs = m.entries.map(e => ({ role: e.role, text: e.text, think: "" }));
      } else if (m.type === "turn_start") {
        this.streaming = true; this.curAi = null; this.curThink = "";
      } else if (m.type === "token") {
        if (!this.curAi) {
          this.curAi = { role: "assistant", text: "", think: "", showThink: false };
          this.msgs.push(this.curAi);
        }
        this.curAi.text += m.delta;
      } else if (m.type === "thinking") {
        if (this.curAi) { this.curAi.think += m.delta; this.curAi.showThink = false; }
      } else if (m.type === "tool_start") {
        this.msgs.push({ role: "assistant", text: `⚙ 调用工具 ${m.tool} …`, think: "" });
      } else if (m.type === "tool_end") {
        this.msgs.push({ role: "assistant", text: "⚙ 工具完成", think: "" });
      } else if (m.type === "agent_event") {
        this.msgs.push({ role: "assistant", text: "📣 " + m.text, think: "" });
      } else if (m.type === "plan_update") {
        this.msgs.push({ role: "assistant",
                         text: "📋 计划：\n" + m.steps.map(s => `  ${s.status === "done" ? "✓" : s.status === "in_progress" ? "◐" : "◻"} ${s.title}`).join("\n"),
                         think: "" });
      } else if (m.type === "run_update") {
        this.run = m.run;
      } else if (m.type === "confirm_request") {
        this.gate = { id: m.id, text: (m.request.title || "") + "\n" + (m.request.message || "") };
      } else if (m.type === "turn_done") {
        this.streaming = false; this.curAi = null; this.loadConvs();
      } else if (m.type === "error") {
        this.msgs.push({ role: "assistant", text: "❌ " + m.message, think: "" });
      }
      this.$nextTick(() => { const el = this.$refs.stream; if (el) el.scrollTop = el.scrollHeight; });
    },
    async send(text) {
      const t = text || this.input.trim();
      if (!t || !this.ws || this.ws.readyState !== 1) return;
      this.msgs.push({ role: "user", text: t, think: "" });
      this.ws.send(JSON.stringify({ type: "message", text: t }));
      this.input = "";
    },
    keydown(e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.send(); }
    },
    answerGate(ok) {
      if (this.ws && this.gate)
        this.ws.send(JSON.stringify({ type: "confirm", id: this.gate.id, confirmed: ok }));
      this.msgs.push({ role: "assistant", text: ok ? "✅ 已确认执行" : "⛔ 已拒绝", think: "" });
      this.gate = null;
    },
    async dropPdf(e) {
      this.drag = false;
      const file = e.dataTransfer.files[0];
      if (!file || !/\.pdf$/i.test(file.name)) return;
      this.status = "上传 PDF…";
      const fd = new FormData();
      fd.append("file", file);
      fd.append("title", file.name.replace(/\.pdf$/i, ""));
      const r = await fetch("/api/papers/pdf", { method: "POST", body: fd,
                                                 credentials: "include" });
      this.status = "已连接";
      if (!r.ok) { alert("PDF 上传失败：" + (await r.text()).slice(0, 200)); return; }
      const p = await r.json();
      this.send(`我上传了论文《${p.title}》（paper_id=${p.id}）。请先用 repro_check 了解它的复现要点并简要汇报。`);
    },
    async openSettings() {
      const r1 = await fetch("/api/settings", { credentials: "include" });
      if (r1.ok) this.settings = await r1.json();
      const r2 = await fetch("/api/chat-config", { credentials: "include" });
      if (r2.ok) this.chatCfg = await r2.json();
      this.settingsOpen = true;
    },
    async saveSettings() {
      await fetch("/api/chat-config", { method: "PUT", headers: { "content-type": "application/json" },
                                        body: JSON.stringify(this.chatCfg), credentials: "include" });
      this.settingsOpen = false;
    },
    async logout() {
      await fetch("/api/logout", { method: "POST", credentials: "include" });
      location.href = "/";
    },
  },
};

createApp(App).mount("#app");
